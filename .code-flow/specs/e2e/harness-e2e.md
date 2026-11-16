---
{
  "description": "测试分层、验收隔离与 CI",
  "next_rule": 1
}
---

# harness-e2e

## Rules

- [RULE-e2e-001] 浏览器 E2E 不再是 CI 门禁：后续需求只需输出可人工执行的场景（`S-*`/`E-*`/`B-*`），由人工在真实构建产物、真实浏览器和真实服务上验证并记录；CI 只运行 unit/integration/contract/component。现有 `e2e/` 套件保留用于本地或按需执行，不作为合并门禁；人工执行成功路径不得改写路由或 mock 业务 API。

## Conventions

**设施与域矩阵**：

- 浏览器用例放 `e2e/tests/**/*.spec.ts`（Playwright + 系统 Chrome channel，**递归**——只扫顶层会漏掉子目录）；前端先 `npm --prefix apps/console-platform/frontend run build` 产出真实构建物。场景命令：`npm --prefix e2e test -- --grep "<场景ID>"`；按域配置加 `--config playwright.<domain>.config.ts`。
- 域配置分三类：**直起真实 Console**（真实 PostgreSQL + lifespan）；foundation contract smoke 与 task-schedule 配置用 `tests.e2e.app`（真实 uvicorn + api-kit 封套）；**`*-ui` / `form-layout` / `list-actions` / `sidebar` 不起任何后端**，只断言前端 UI（布局、可访问性、错误态）。**E2E 中不得 mock 业务 API**（component 套件除外）。
- **只启本次真正用到的服务**：只读聚合页只起 Console + preview + 必要探针，不启 Runtime/Worker/Gateway；租户与产物根钉在 `os.tmpdir()`，经 `DEFAULT_TENANT_ID` / `ARTIFACT_ROOT` 注入，不写仓库 `.data/artifacts`；租户随端口 OFFSET 变化，保证相邻场景互不可见。
- 外部依赖（模型/LLM、第三方 API）用真实本地探针：`tests/e2e/openai_probe_app.py` 与 Console/Vite 并列写入 `webServer`；禁止在 E2E 中伪造外部响应。

**域配置口径**：

- 前端跑**真实构建产物**而非 dev server：`vite preview` + `MUAD_API_TARGET` 指向本域 Console；`preview` 必须带 `--strictPort`（端口被占要显式失败，不能静默另择端口而 baseURL 仍指原端口）。域配置里的 `${...}` 不得写在**单引号**字符串里——JS 不插值，字面量会被 shell 展开为空。机检：`tests/frontend/test_e2e_suite_contract.py`。
- **启动前停 dev 服务**：`e2e/package.json` 的 `pretest` 调 `scripts/dev.sh stop`；否则 `--reload` Worker/Runtime 会抢共享 dev 库任务，表现为 `SKILL_ARTIFACT_UNAVAILABLE` 一类假故障。Gateway 的入口快照节拍由环境配置定义（验收与生产不同）。
- **域配置必须钉死 `use.timezoneId`**：`DateTimeText` 按浏览器本地时区渲染，CI runner 是 UTC、开发机是 UTC+8，不钉会出现「本地绿、CI 红」且报错只说找不到文本。全部配置钉 `Asia/Shanghai`（与 `locale: 'zh-CN'` 同属「谁在看」）。机检：`test_domain_configs_pin_browser_timezone`。
- **e2e 定位符必须引用前端源码真实存在的 testid**：模板形式（`data-testid={\`mcp-tool-${name}\`}`）按前缀匹配；改名/删除 testid 必须同步。机检：`test_e2e_testids_are_defined_in_frontend_source`。
- **端口偏移**：`端口 = 基址 + OFFSET`，OFFSET 取 argv 里的 `[SE]-\d+` 场景号，无 `--grep` 回落 `pid % 47`；**必须算一次后冻结进 `process.env`**——`config` 会在每个 worker 里重新求值，而 worker argv 不含 `--grep`，不冻结会算出与主进程不同的端口。
- **共用租户/共享种子的域配置必须 `workers: 1`**：`beforeAll` 播种、`afterAll` 清理同一份租户级种子，并行会互删数据（现有 13 份：audit-observability、console-auth、overview-dashboard、settings、run-observability×3、task-schedule×3、webchat×3）。
- **E2E 创建的业务数据必须可识别并清理**：用 `e2e-` 等 key 前缀，在 spec 清理钩子或独立清理步骤中删 DB 记录；上传类资源用孤儿清理入口（`cleanup-skill-orphans --grace-seconds 0`）。否则共享开发库会变成"假数据看板"。

**分层：关键 / 回归 / smoke**：

- 各域按「真实浏览器 + 真实后端 + 真实 DB 才能证明」筛选 E2E，其余下沉 component（mock API）或 API/集成层。已收口：console-auth 5 条旅程、task-schedule 5 关键 + 26 回归、webchat 12 关键 + 32 回归、run-observability 3 关键 + 11 回归；model/skill/mcp/project-platform/user/settings/agent 均按「真实创建/绑定/持久化留 E2E，布局/错误态/管理动作进 `*-ui`」拆分。全量配置保留给本地/按需执行与人工验收，关键/回归用 `grep`/`grepInvert` 互补。
- **smoke 白名单**登记在 `e2e/scenarios/smoke-15.scenarios.md`（全部 `smoke: true`），覆盖登录/RBAC、概览、模型池、Agent、Skill、MCP、平台/凭据、用户、设置、任务取消、定时任务、审计导出、Run↔Task、Webchat 两人旅程；`make smoke`（一次 build、逐域 `--grep "@smoke"`、fail-fast）仅用于本地按需自检，不再是 CI 门禁。
- **非 smoke 关键旅程**登记在 `key-journeys-non-smoke.scenarios.md`，全部带 `trigger_paths` 与 `why_e2e`；`scripts/e2e_changed.py` 按 `trigger_paths` 与 `git diff --name-only <base>` 匹配，`make e2e-changed` 仅用于本地按需执行，不再由 CI PR 触发。
- **回归套件**（task 26 / webchat 32 / run 11）登记在 `regression-suites.md`，保留给本地/按需整跑；CI 不再跑 push 全量 e2e。
- **build-once**：Makefile 支持 `BUILD_FRONTEND=0`；CI 的 component job 先 build 一次，再逐域 `BUILD_FRONTEND=0`。
- **归档快照**：`docs/operations/e2e-archive-snapshots.md` 明确归档 manifest/覆盖表不回改；重放归档验收按 live 注册表与 `design_refs` 映射。

**CI 执行面与人工验收**：

- CI 不再运行浏览器 E2E；原 `smoke`/`e2e` job 已移除。CI 门禁是 unit/integration/contract/component。
- 现有 `e2e/` 代码保留用于本地或按需执行（`make acceptance-e2e DOMAIN=...` / `make smoke`），不作为合并门禁。
- 每个需求在场景文件中输出可执行步骤与期望结果（`S-*`/`E-*`/`B-*`）；人工在真实构建、真实浏览器和真实服务上执行，记录日期、环境与结果。未执行的人工场景必须保持“待验证”，不得用组件测试、静态接线或旧结果冒充通过。
- 文案与容器类（`.semi-*`）漂移静态不可判，只能由按需实跑或人工验收抓；机检不是「漂移全防」的替代品。

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

## Applied Deltas

- {"source":"2026-11-15/scheduled-task-v2/scheduled-task-v2.md#TASK-033#DELTA-001","op":"update","rule":"RULE-e2e-001","payload_sha256":"189d2b156c3ffe93da68b36f3aab573d37ca2a537073f5d15fca3023dfc7d3a5","at":"2026-11-16T04:24:01.418Z"}

- {"source":"2026-11-15/scheduled-task-v2/scheduled-task-v2.md#TASK-033#DELTA-002","op":"add","rule":"RULE-001","payload_sha256":"6508d098d7d383247a17ae0930e4be3c0b41a86f345aebc600b940222dc37b77","at":"2026-11-16T04:24:01.419Z"}
