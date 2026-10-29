---
id: harness-e2e
description: 测试分层、验收隔离与 CI
stages: [design, plan, code, review]
enforcement: required
verifiers:
- rule: RULE-e2e-001
  type: command
  config:
    argv: [uv, run, pytest, -q, tests/frontend/test_e2e_suite_contract.py]
    cwd: .
    timeout: 300
---

# harness-e2e

## Rules

- [RULE-e2e-001] 浏览器 E2E 必须使用真实构建产物与真实浏览器：成功路径（`S-*`）不得改写路由或 mock 业务 API；失败/边界路径（`E-*`）允许制造超时/错误，但不得 fulfill 业务响应体。域配置必须 `channel: 'chrome'`、`preview --strictPort`、钉死浏览器时区，并只启动该域实际依赖的服务。

## Conventions

**设施与域矩阵**：

- 浏览器用例放 `e2e/tests/**/*.spec.ts`（Playwright + 系统 Chrome channel，**递归**——只扫顶层会漏掉子目录）；前端先 `npm --prefix apps/console-platform/frontend run build` 产出真实构建物。场景命令：`npm --prefix e2e test -- --grep "<场景ID>"`；按域配置加 `--config playwright.<domain>.config.ts`。
- 33 份域配置分三类：**16 份直起真实 Console**（真实 PostgreSQL + lifespan）；`playwright.config.ts`（foundation contract smoke，4 条）与 task-schedule 三配置用 `tests.e2e.app`（真实 uvicorn + api-kit 封套）；**13 份 `*-ui` / `form-layout` / `list-actions` / `sidebar` 不起任何后端**，只断言前端 UI（布局、可访问性、错误态）。**E2E 中不得 mock 业务 API**（component 套件除外）。
- **只启本次真正用到的服务**：只读聚合页只起 Console + preview + 必要探针，不启 Runtime/Worker/Gateway；租户与产物根钉在 `os.tmpdir()`，经 `DEFAULT_TENANT_ID` / `ARTIFACT_ROOT` 注入，不写仓库 `.data/artifacts`；租户随端口 OFFSET 变化，保证相邻场景互不可见。
- 外部依赖（模型/LLM、第三方 API）用真实本地探针：`tests/e2e/openai_probe_app.py` 与 Console/Vite 并列写入 `webServer`；禁止在 E2E 中伪造外部响应。

**域配置口径**：

- 前端跑**真实构建产物**而非 dev server：`vite preview` + `MUAD_API_TARGET` 指向本域 Console；`preview` 必须带 `--strictPort`（端口被占要显式失败，不能静默另择端口而 baseURL 仍指原端口）。域配置里的 `${...}` 不得写在**单引号**字符串里——JS 不插值，字面量会被 shell 展开为空。机检：`tests/frontend/test_e2e_suite_contract.py`。
- **域配置必须钉死 `use.timezoneId`**：`DateTimeText` 按浏览器本地时区渲染，CI runner 是 UTC、开发机是 UTC+8，不钉会出现「本地绿、CI 红」且报错只说找不到文本。全部配置钉 `Asia/Shanghai`（与 `locale: 'zh-CN'` 同属「谁在看」）。机检：`test_domain_configs_pin_browser_timezone`。
- **e2e 定位符必须引用前端源码真实存在的 testid**：模板形式（`data-testid={\`mcp-tool-${name}\`}`）按前缀匹配；改名/删除 testid 必须同步。机检：`test_e2e_testids_are_defined_in_frontend_source`。
- **端口偏移**：`端口 = 基址 + OFFSET`，OFFSET 取 argv 里的 `[SE]-\d+` 场景号，无 `--grep` 回落 `pid % 47`；**必须算一次后冻结进 `process.env`**——`config` 会在每个 worker 里重新求值，而 worker argv 不含 `--grep`，不冻结会算出与主进程不同的端口。
- **共用租户/共享种子的域配置必须 `workers: 1`**：`beforeAll` 播种、`afterAll` 清理同一份租户级种子，并行会互删数据（现有 13 份：audit-observability、console-auth、overview-dashboard、settings、run-observability×3、task-schedule×3、webchat×3）。
- **E2E 创建的业务数据必须可识别并清理**：用 `e2e-` 等 key 前缀，在 spec 清理钩子或独立清理步骤中删 DB 记录；上传类资源用孤儿清理入口（`cleanup-skill-orphans --grace-seconds 0`）。否则共享开发库会变成"假数据看板"。

**分层：关键 / 回归 / smoke**：

- 各域按「真实浏览器 + 真实后端 + 真实 DB 才能证明」筛选 E2E，其余下沉 component（mock API）或 API/集成层。已收口：console-auth 5 条旅程、task-schedule 5 关键 + 26 回归、webchat 12 关键 + 32 回归、run-observability 3 关键 + 11 回归；model/skill/mcp/project-platform/user/settings/agent 均按「真实创建/绑定/持久化留 E2E，布局/错误态/管理动作进 `*-ui`」拆分。全量配置保留给归档命令与手工整跑，关键/回归用 `grep`/`grepInvert` 互补。
- **15 条 smoke 白名单**登记在 `e2e/scenarios/smoke-15.scenarios.md`（全部 `smoke: true`），覆盖登录/RBAC、概览、模型池、Agent、Skill、MCP、平台/凭据、用户、设置、任务取消、定时任务、审计导出、Run↔Task、Webchat 两人旅程；`make smoke`（一次 build、逐域 `--grep "@smoke"`、fail-fast）与 CI `smoke` job 只跑这 15 条。
- **非 smoke 关键旅程**（25 个）登记在 `key-journeys-non-smoke.scenarios.md`，全部带 `trigger_paths` 与 `why_e2e`；`scripts/e2e_changed.py` 按 `trigger_paths` 与 `git diff --name-only <base>` 匹配，`make e2e-changed` / CI PR 的 smoke job 以 `--no-smoke --run` 执行命中域（push 仍跑完整 e2e）。smoke 永远先跑。
- **回归套件**（task 26 / webchat 32 / run 11）登记在 `regression-suites.md`，不进 PR smoke/changed，由 push 的完整 e2e job 执行。
- **build-once**：Makefile 支持 `BUILD_FRONTEND=0`；CI 的 e2e/component job 各先 build 一次，再逐域 `BUILD_FRONTEND=0`。
- **归档快照**：`docs/operations/e2e-archive-snapshots.md` 明确归档 manifest/覆盖表不回改；重放归档验收按 live 注册表与 `design_refs` 映射。

**CI 执行面**：

- CI `e2e` job 逐域顺序跑全部 16 个入口（13 真实域 + 三个关键/回归拆分）、**不 fail-fast**，一次列出全部漂移域；纯 mock 的 13 个域在独立 `component` job 顺序跑，不占 E2E 信号与超时预算。每个失败域**当场**把 `e2e/test-results`（trace + `error-context.md`）搬进 artifact——Playwright 每次 run 清空 `outputDir`，不搬只剩最后一个域。
- 文案与容器类（`.semi-*`）漂移静态不可判，只能由各域实跑抓；机检不是「漂移全防」的替代品，兜底是 CI 的 `e2e` job。

✅ 真实边界（浏览器链路端到端）：

```bash
npm --prefix apps/console-platform/frontend run build && npm --prefix e2e test -- --grep "S-11"
# 断言真实 Chrome：localStorage 语言持久化 + /auth/me 请求头 X-Locale=en-US
```

❌ 成功路径出现路由拦截（链路退化为 mock）：

```ts
await page.route('**/api/v1/**', (route) => route.fulfill({ json: { code: '0' } }));
```

- **成功路径（`S-*`）不得出现 `page.route(`**：只有失败/边界路径（`E-*`）允许改写路由制造超时/错误/非 2xx，且不得 fulfill 业务响应体。机检：收口清单按场景块扫描该字符串。

## Avoid

- 违反上述任一规则的实现必须修复；与此 Spec 冲突的文档以本 Spec 与 `docs/` V1.4 为准。
