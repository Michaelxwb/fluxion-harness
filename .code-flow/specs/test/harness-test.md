---
id: harness-test
description: Agent Harness 通用平台规则：test
stages:
- design
- plan
- code
- review
enforcement: required
verifiers:
- rule: RULE-test-001
  type: command
  config:
    argv:
    - bash
    - -lc
    - uv run pytest -q tests/acceptance && npm --prefix apps/console-platform/frontend
      run build && npm --prefix e2e test
    cwd: .
    # 链 = tests/acceptance + 前端 build + Playwright e2e。2026-09-29：该链在 1200s 下被
    # `verifier_timeout` 掐断（Done Gate 报 `harness-test#RULE-test-001: unverified`）。
    # 注意该次观测时共享库上存在一套**孤儿验收栈**（PPID=1），其 Worker 会抢走验收任务
    # （claim 无 tenant 谓词）从而放大耗时（同批用例清孤儿前后 369s → 56s），故不能据此
    # 断定链本身需要 1200s 以上。清孤儿后放宽到 2400s 并实测通过（Done Gate pass，
    # 2026-09-29 15:07）；真实链耗时待下次干净单跑补记。规则文本未改，只调 config。
    timeout: 2400
---

# harness-test

## Conventions

E2E 创建的业务数据（无删除端点的资源尤其如此）必须：用可识别 key 前缀（如 `e2e-`），并在 spec 的清理钩子或独立清理步骤中删除 DB 记录；否则会污染共享开发库，把真实页面变成"假数据看板"（05 遗留 70 条 `e2e-*` Skill 与用户下拉污染）。上传类资源配套用孤儿清理入口回收文件：`PYTHONPATH=apps/console-platform/backend/src uv run python -c "from muad_console_platform.cli import main; main(['cleanup-skill-orphans','--grace-seconds','0'])"`。

## Rules

- [RULE-test-001] 跨 API/DB/Runtime/Browser 的关键流程必须 E2E 且明确“不得 mock 的真实边界”（真实 PostgreSQL、真实 Redis 行为、真实 HTTP、真实浏览器渲染）；单元测试覆盖纯逻辑与状态机，契约测试覆盖枚举/错误码/迁移一致性。

## Conventions

标准 E2E 设施（不得自建临时浏览器脚本）：

- 浏览器用例放 `e2e/tests/**/*.spec.ts`（Playwright + 系统 Chrome channel，**递归**——20 个 spec 里 16 个在子目录如 `e2e/tests/task-schedule/`，只扫顶层会漏掉绝大部分），前端先 `npm --prefix apps/console-platform/frontend run build` 产出真实构建物。
- 后端**不止** `tests/e2e/app.py`：10 份域配置里 **9 份直起真实 Console**（`uv run uvicorn muad_console_platform.main:app`，真实 PostgreSQL + 真实 lifespan），只有 `playwright.task-schedule.config.ts` 用 `tests.e2e.app`（真实 uvicorn + api-kit 封套/会话原语 + 真实静态产物）。选中哪条取决于该域是否需要产品主干之外的桩；无论哪条，**E2E 中不得 mock 业务 API**。
- 前端跑**真实构建产物**而非 dev server：用 `npm run preview` 起 `vite preview`，并把 `MUAD_API_TARGET` 指向该域的 Console 地址（`webServer` 里以环境变量注入，同时起到把前端流量钉到本域实例上的作用）。因此域套件**必须先 `npm run build`**，否则 preview 拿到的是陈旧产物。
- 场景命令统一为 `npm --prefix apps/console-platform/frontend run build && npm --prefix e2e test -- --grep "<场景ID>"`；按域配置时用 `npm --prefix e2e test -- --config playwright.<domain>.config.ts --grep "<场景ID>"`。
- **域配置的端口偏移口径**：`端口 = 基址 + OFFSET`（如 audit-observability 的三个基址 8301/8401/8501），OFFSET 优先取本进程 argv 里的 `[SE]-\d+` 场景号，无 `--grep` 时回落 `pid % 47`；**且必须算一次后冻结进 `process.env`**——`config` 会在每个 worker 里被重新求值，而 worker 的 argv 不含 `--grep`，不冻结就会算出与主进程不同的端口、连不上已经起来的服务。5 份配置同口径（`playwright.audit-observability.config.ts` 等）。
- **共用租户/共享种子的域配置必须 `workers: 1`**：这些套件在 `beforeAll` 播种、`afterAll` 清理同一份租户级种子，并行会互相清掉对方的数据（现有 4 份：`playwright.audit-observability.config.ts`、`playwright.console-auth.config.ts`、`playwright.overview-dashboard.config.ts`、`playwright.task-schedule.config.ts`，配置里均有注释说明理由）。
- **只启本次真正用到的服务，且不污染仓库**：模块 E2E 只拉起它实际依赖的进程（如只读聚合页只起 Console + preview + 必要探针，**不启** Runtime/Worker/Gateway），租户与产物根钉在系统临时目录（`os.tmpdir()`），经 `DEFAULT_TENANT_ID` / `ARTIFACT_ROOT` 注入被测服务，**不写仓库 `.data/artifacts`**。租户随 OFFSET 变化，保证并行/相邻场景互不可见。
- 外部依赖（模型/LLM 端点、第三方 API）用真实本地探针服务承载：`tests/e2e/openai_probe_app.py`（真实 HTTP 健康响应）与 Console/Vite 并列写入 `webServer` 数组；禁止在 E2E 中伪造外部响应。
- **流式超时的「有界失败」用例必须注入 `STREAM_TIMEOUT_SEC`**：`RuntimeClient` 的流式读超时由**独立常量**决定——`create_run` 显式传 `httpx.Timeout(STREAM_TIMEOUT_SEC, connect=REQUEST_TIMEOUT_SEC)`，构造函数的 `timeout_sec` 只作用于非流式调用（`apps/im-gateway/src/muad_im_gateway/application/runtime_client.py`）。常量默认 300s，用例须 `monkeypatch` 成小值才能驱动「上游挂起 → 有界失败」的真实路径（`tests/gateway/test_runtime_client.py`）。
- **每个需求收尾必须有 `tests/<domain>_inventory.py` 闭合清单**：以**真实盘面**为输入做交叉核对，而不是自查断言——任务文档的覆盖表/契约表/Evidence 表、`.acceptance-manifest.json`、`spec-context.yml` 里的 required 规则，以及 E2E 套件与场景名是否**真的在盘**。口径（4 例：`tests/console_auth_inventory.py`、`tests/overview_dashboard_inventory.py`、`tests/audit_observability_inventory.py`、`tests/dfx_inventory.py`）：
  - 覆盖表每行（含 RULE 规则行）唯一负责人且终态，**且不豁免收口任务自身**——只豁免它会让「收口任务自己永远停在进行中」静默通过（13-console-auth 归档前的复发点）；manifest 与覆盖表同 ID / 同 owner / 同命令，**并比对 `level`/`boundary`/`cwd`**。
  - 终态场景与规则行在该 owner 的 Evidence 小节里登记且状态一致；**非 manual 行必须有同 ID 的证据表行**——runner 自动写的 `- <ID>: <status> — …` 条目**不能替代**（它会让「整行缺失」被自动条目掩盖；只有 manual 行接受条目形态）。
  - **契约表要为该任务 `Acceptance-Refs` 里的每个 ID 都留一行**：只断言「行是否终态」查不出**整行被删**（行没了就没有非终态可查，静默通过）。
  - 证据表不得残留占位行（`编码期填写`/`TBD` 一类）；`-k`/`-g` 令牌必须在真实用例名/真实 spec 文件里命中。
  - **结构性 RED + 扰动取证**：清单文件缺失时登记的 argv 必须失败（否则「先写用例后建清单」是空话）；至少扰动 4 类——改状态、删证据行、伪造用例名或命令、改 manifest 字段——每类须变红且消息指名条目，**逐字节还原后复跑全绿**。
  - **路径双写 live→archived**：`_dir()` 先试 `.code-flow/tasks/<日期>/<需求>`，不存在再取 `archived/`——硬编码 live 路径会让归档后整个验收套件变红（09/11/13 均踩过）。
  - ❌ 只断言「本文件里的清单已勾选」——那是对自查结果自查，任务文档写错时清单照样绿。

✅ 真实边界（浏览器链路端到端）：

```bash
npm --prefix apps/console-platform/frontend run build && npm --prefix e2e test -- --grep "S-11"
# 断言真实 Chrome：localStorage 语言持久化 + /auth/me 请求头 X-Locale=en-US
```

❌ 不允许：

```ts
// 成功路径（S-*）出现路由拦截即违规：链路退化为 mock，页面"通过"不代表真实链路可用
await page.route('**/api/v1/**', (route) => route.fulfill({ json: { code: '0' } }));
```

- 用 jsdom / 组件单测冒充 E2E，或未渲染真实浏览器即标记 E2E 场景。
- **成功路径（`S-*`）不得出现 `page.route(`**：只有失败/边界路径（`E-*`）允许改写路由，且**不得 fulfill 业务响应体**（改写仅用于制造超时/错误/非 2xx 等边界，不能替换真实业务数据）。机检：`tests/console_auth_inventory.py`、`tests/overview_dashboard_inventory.py` 均在收口清单里按场景块扫描该字符串。

## Conventions

Runtime 验收真实环境基建（`tests/acceptance/runtime/`）：

- 用 module-scoped live stack 承载真实边界：Console/Runtime 为真实 uvicorn 服务、LLM/MCP 为本地 HTTP 探针、数据落真实 PostgreSQL/Redis/Artifact Store；禁止 `dependency_overrides`/mock 业务服务。
- 跨 Pod/崩溃场景用真实子进程（`SIGKILL` 后由另一实例 Reaper 回收、同 conversation 无 sticky session 接管）；进程内服务线程与 pytest-asyncio 并存时，须在套件边界清理 engine `lru_cache`，async 工具走独立线程/事件循环（禁止 `asyncio.run` 污染主循环）。
- 验收命令由 acceptance manifest runner 统一复验并写证据：相同 argv/cwd/timeout 复用，测试未收集、未执行或失败均视为 FAIL。
- **不得用 `--output` 重生成 manifest**：`cf_acceptance_manifest.py --task-file … --output …` 会**清空已有 `evidence`**（实测一次把 23 行打成 2 行，直到需求级终验才以 `functional_or_manual_evidence_missing` 暴露，白跑一整轮）；需求进行中改任务文档**不要**走重生成。已清空时按 owner 回填：`cf_acceptance_runner.py --manifest <需求目录>/.acceptance-manifest.json --root "$PWD" --write-evidence --owner TASK-0XX`（**无需 active marker**；不带 `--include-e2e` 时 E2E 行保持 deferred）。

✅ 真实进程 + 真实探针：

```python
with httpx.stream("POST", f"{runtime.url}/v1/runs", json=payload) as stream:
    for line in stream.iter_lines():          # 真实 SSE
        ...
pod_a.kill()                                   # 真实进程终止 → Reaper 回收
```

❌ 直连 DB 冒充 E2E（声称真实 Gateway/HTTP/SSE，实际只 seed 表）：

```python
await session.execute(sa.update(RunRecord).where(...).values(status="RUNNING"))  # 无 HTTP/SSE/执行链
assert (await session.get(RunRecord, run_id)).status == "RUNNING"                # 恒真
```

## Conventions

验收反模式（Anti-Patterns）：

- ❌ 恒真断言：`assert reaped >= 0`、对可能为空集合 `all(...)`、`exclude={"api_key"}` 后再断言无 `api_key`——断言必须能真实失败。
- ❌ 命令与场景不符：`-k` 未命中任何测试（退出码 5）却标记 verified；或测试名/文件与 Acceptance Contract 声明的路径不一致。
- ❌ 验收层级注水：把 service/DB 级测试写成 E2E，或边界文案（真实 Gateway/进程/SSE）与测试实际行为不符。
- ❌ 空转用例：测试名为"跨 host 跳转拒绝"却请求 `/healthz`、`follow_redirects=False` 从未触发跳转。
- ❌ **多输入断言的腿被合并**：断言「改配置/授权只影响新 Run」这类跨腿效果时，必须**一次只改一个输入**、并断言其余腿对应的列不变；否则断言恒真、扰动打不出来。
  - ✅ 逐腿：先只改 `agent.instructions`，断言新 Run 的 `content_hash` 变**且** model 相关列不变；再只改 `model.params_json`，反向断言。
  - ❌ 同时改 agent+model 后只断言「hash 变了」——即便把 `agent` 从 hash 计算里去掉，断言照样绿（实测首轮扰动不变红，加固为逐腿后才变红）。参考 `tests/acceptance/dfx/test_dfx_stateless.py`、`tests/agent_runtime/test_snapshot_freeze.py::test_b104_definition_change_only_affects_new_runs`。
- ❌ 把验收/Done Gate 命令的输出接进会**提前关闭的管道**（典型 `| head`）：SIGPIPE 会打断 pytest 收尾，`stop_live_stack`/`stop_audit_stack` 不执行，留下 uvicorn/`muad_*.main` 孤儿进程继续连同一个本地测试库 → 后续运行随机失败（如 `SKILL_ARTIFACT_UNAVAILABLE`、任务 `FAILED`），且失败点每次不同、单跑却都通过，极易误判为跨模块 flake。用 `> file` 或 `tail`（会读完输入）；每次运行前先确认无残留进程（`ps aux | grep -E "[u]vicorn|muad_(agent_worker|agent_runtime|console_platform|im_gateway)\.main"`）。**登记缺口：本条无任何脚本/机检约束**——收口清单里没有对应的规则断言，属纯人工纪律，只能在评审与运行前自查时人工把关。

## Avoid

- 违反上述任一规则的实现必须修复；与此 Spec 冲突的文档以本 Spec 与 `docs/` V1.4 为准。
