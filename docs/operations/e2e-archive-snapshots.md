# 归档验收快照（E2E 分层后）

2026-10-23 的 E2E 精度改造把各域关键旅程收窄、把 UI/错误态下沉 component、把非关键真实栈
用例拆成 regression。归档任务（`.code-flow/tasks/archived/**`）里的
`.acceptance-manifest.json`、覆盖表命令与 Evidence 是**当时**的验证快照，**不回改**。

## 口径

- **归档文档是历史事实**：它们记录的是当时的套件与命令，不能因为今天的 spec 被裁剪就重写。
- **重放归档验收不要直接跑归档 manifest**：部分命令仍指向被裁剪/移动的旧 spec（如
  `console-auth.spec.ts -g "S-01"`、`task-schedule/environment.spec.ts`、`webchat/*` 的旧全量命令）。
- **重放时按 live 注册表映射**：
  - `e2e/scenarios/smoke-15.scenarios.md` — 15 条 smoke 白名单；
  - `e2e/scenarios/key-journeys-non-smoke.scenarios.md` — 25 个非 smoke 关键旅程（含 `trigger_paths`）；
  - `e2e/scenarios/regression-suites.md` — task/webchat/run 非 smoke 回归；
  - `tests/acceptance/**` — 真实 DB/事务/幂等/安全的 integration；
  - `*-ui.spec.ts` + `playwright.*-ui.config.ts` — mock component。
- **场景 ID 不重新编号**。迁移后的同名场景/用例标题保留原 ID，`design_refs`（如
  `console-auth#S-15`）是映射入口。
- **live 收口清单继续守当前盘面**：`tests/console_auth_inventory.py`（E2E/component/acceptance
  三处映射 + 注册表防漂移）、`tests/audit_observability_inventory.py`、
  `tests/overview_dashboard_inventory.py` 仍按归档覆盖表核对仍在 E2E 的场景。
- **不要用 `cf_acceptance_manifest.py --output` 重生成归档 manifest**：会清空 evidence，
  且会把历史命令替换成当前盘面。

## 已知失效的归档命令（示例）

| 归档位置 | 旧命令指向 | 现状/替代 |
|---|---|---|
| `archived/2026-09-17/13-console-auth` | `console-auth.spec.ts -g "S-01"` 等 | S-01/S-03..S-06 已下沉 acceptance/component；E2E 只保留 S-09/S-10/E-09/E-11/S-15 |
| `archived/2026-09-17/09-task-schedule` | `tests/task-schedule/environment.spec.ts` | 文件已删除；环境断言由 `tests/acceptance/task_schedule/test_environment.py` 的 B-139 覆盖 |
| `archived/2026-09-17/12-overview-dashboard` | `-g "S-05"/"E-02".."E-09"` | 已下沉 `overview-dashboard-ui.spec.ts`；E-02 删除（与 task E-FE-04 重复） |
| `archived/2026-09-17/11-audit-observability` | `-g "E-06".."E-09"` | 已下沉 `audit-observability-ui.spec.ts` |
| webchat/task/run 归档 | 旧全量 spec 命令 | 关键/回归配置见 `e2e/scenarios/regression-suites.md` 与各 `*-e2e` 配置 |

## 结论

归档 manifest 不再作为可执行验收入口；**live 注册表 + 当前配置**才是。若确实需要复验某个
归档需求，按 `design_refs` 找到当前层与命令，再执行。
