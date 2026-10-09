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

- 浏览器用例放 `e2e/tests/**/*.spec.ts`（Playwright + 系统 Chrome channel，**递归**——25 个 spec 里 16 个在子目录如 `e2e/tests/task-schedule/`，只扫顶层会漏掉绝大部分），前端先 `npm --prefix apps/console-platform/frontend run build` 产出真实构建物。
- 后端**不止** `tests/e2e/app.py`：13 份域配置里 **9 份直起真实 Console**（`uv run uvicorn muad_console_platform.main:app`，真实 PostgreSQL + 真实 lifespan），`playwright.task-schedule.config.ts` 用 `tests.e2e.app`（真实 uvicorn + api-kit 封套/会话原语 + 真实静态产物），另 **3 份（`form-layout` / `list-actions` / `sidebar`）不起任何后端**、只断言前端 UI（布局、刷新按钮可访问性、筛选控件宽度等）。选中哪条取决于该域是否需要产品主干之外的桩；无论哪条，**E2E 中不得 mock 业务 API**。
- 前端跑**真实构建产物**而非 dev server：用 `npm run preview` 起 `vite preview`，并把 `MUAD_API_TARGET` 指向该域的 Console 地址（`webServer` 里以环境变量注入，同时起到把前端流量钉到本域实例上的作用）。因此域套件**必须先 `npm run build`**，否则 preview 拿到的是陈旧产物。机检：`tests/frontend/test_e2e_suite_contract.py`（域配置不得出现 `run dev`；`preview` 必须带 `--strictPort`，否则端口被占会静默另择端口而 baseURL 仍指原端口；同时起真实 Console 的域必须注入 `MUAD_API_TARGET`；Makefile 的 `acceptance-e2e` 必须先 build 再跑；域配置里的 `${...}` 不得写在**单引号**字符串里 —— JS 不插值，字面量会被原样交给 shell 并展开为空，2026-10-06 platform 域实测：`uvicorn … --port ${apiPort}` 变成 `--port ` ⇒ `[WebServer] Error: Option '--port' requires an argument.`，e2e job 连红两轮且报错看不出根因）。2026-10-02 前 13 份配置里有 5 份跑的是 dev server（`agent`/`mcp`/`skill`/`platform`/`list-actions`），正是这条无约束导致的漂移。
- **域配置必须钉死浏览器时区（`use.timezoneId`）**：展示值按**浏览器本地**时区渲染（`DateTimeText` 用 `getFullYear()`/`getHours()` 逐段拼装，口径见 `harness-time.md`），不钉就出现「同一份断言**本地绿、CI 红**」——CI runner 是 UTC、开发机是 UTC+8，而失败信息只报「找不到该文本」，从报错看不出是时区。13 份配置一律钉 `Asia/Shanghai`，与既有的 `locale: 'zh-CN'` 同属「谁在看」的口径。
  - ✅ `use: { baseURL: ..., channel: 'chrome', locale: 'zh-CN', timezoneId: 'Asia/Shanghai' }`
  - ❌ 只钉 `locale` 不钉 `timezoneId`，然后断言 `2026-12-31 09:00:00`（种子 `2026-12-31T01:00:00+00:00`）⇒ 本地必绿、CI 必红（2026-10-02 task-schedule 的 B-135/B-136 实测：本地 `TZ=UTC` 复现 `2 failed`，加 pin 后 `27 passed`）。
  - 机检：`tests/frontend/test_e2e_suite_contract.py::test_domain_configs_pin_browser_timezone`。
- **e2e 定位符必须引用前端源码中真实存在的 testid**：实现改名或删除 testid 后用例必须同步，否则断言只在跑该域时才红、没有任何聚合信号。源码中的模板形式（``data-testid={`mcp-tool-${name}`}``）按**前缀**匹配以覆盖数据后缀。机检：`tests/frontend/test_e2e_suite_contract.py::test_e2e_testids_are_defined_in_frontend_source`。
- 断言里的**文案与容器类**漂移（如容器从 `.semi-modal` 换成 `.semi-sidesheet`、写死的英文标签被本地化）**静态不可判**：`.semi-*` 是库类（不在本仓源码内），渲染结果依赖真实组件。这类只能由各域套件实跑来抓 —— 机检不是「漂移全防」的替代品。**兜住这类的是 CI 的 `e2e` job**（`.github/workflows/check.yml`，2026-10-02 增加）：逐域顺序跑全部 13 个域、**不 fail-fast**，一次 CI 就列出**全部**漂移的域；此前 CI 只有 `npm ci && npm run build`，Playwright 从不执行。该 job 在**每个失败域当场**把 `e2e/test-results`（trace + `error-context.md`）搬进 artifact —— Playwright 每次 run 都会清空 `outputDir`，不搬则 13 个域顺序跑完只剩**最后一个**域的，偶发失败（如 `task-cancel` 的 popconfirm 按钮反复 detach——2 轮 CI 里出现 1 次、本地 13 次未复现）事后无从复盘。
- 场景命令统一为 `npm --prefix apps/console-platform/frontend run build && npm --prefix e2e test -- --grep "<场景ID>"`；按域配置时用 `npm --prefix e2e test -- --config playwright.<domain>.config.ts --grep "<场景ID>"`。
- **域配置的端口偏移口径**：`端口 = 基址 + OFFSET`（如 audit-observability 的三个基址 8301/8401/8501），OFFSET 优先取本进程 argv 里的 `[SE]-\d+` 场景号，无 `--grep` 时回落 `pid % 47`；**且必须算一次后冻结进 `process.env`**——`config` 会在每个 worker 里被重新求值，而 worker 的 argv 不含 `--grep`，不冻结就会算出与主进程不同的端口、连不上已经起来的服务。5 份配置同口径（`playwright.audit-observability.config.ts` 等）。
- **共用租户/共享种子的域配置必须 `workers: 1`**：这些套件在 `beforeAll` 播种、`afterAll` 清理同一份租户级种子，并行会互相清掉对方的数据（现有 4 份：`playwright.audit-observability.config.ts`、`playwright.console-auth.config.ts`、`playwright.overview-dashboard.config.ts`、`playwright.task-schedule.config.ts`，配置里均有注释说明理由）。
- **只启本次真正用到的服务，且不污染仓库**：模块 E2E 只拉起它实际依赖的进程（如只读聚合页只起 Console + preview + 必要探针，**不启** Runtime/Worker/Gateway），租户与产物根钉在系统临时目录（`os.tmpdir()`），经 `DEFAULT_TENANT_ID` / `ARTIFACT_ROOT` 注入被测服务，**不写仓库 `.data/artifacts`**。租户随 OFFSET 变化，保证并行/相邻场景互不可见。
- **验收每轮独占一个空库 + 一个独立 Redis DB，且必须自动生效（不需要任何显式指定）**：不能共用 dev 库 —— 任务队列的 claim 与投递选取**都不带租户谓词**（Worker 是无状态通用工作池，任务行自带 `tenant_id`：`worker/claimer.py:20-27`、`delivery/service.py:120-139`），而各栈的**出站渠道端点是栈级 env**（`CHANNEL_PROBE_URL` ⇒ `im-gateway/main.py:35-39`）。于是别的套件（或 dev 服务）留下的可领取/待投递行会被本栈领走、并按本栈端点投递：表现为「我的探针里出现别人的投递」，断言随机红且**单跑绿、串跑红**（2026-10-02 实测：`verify-e2e` 的 14 条 verifier 顺序执行下 task_schedule 的投递用例必红，单独跑全绿）。
  - ✅ 每轮建 `muad_acc_<uuid>` + `alembic upgrade head`（与 CI 同口径：现场生成临时 ini 用 `migrations/db_migrate.py -c`），Redis 在 10–15 号里**原子占位**（`SET NX EX`；收尾归还，异常中断由 2h TTL 兜底；号位全占满时**显式失败**，不静默退回共享）；pytest 侧由 `tests/acceptance/conftest.py` 的 session fixture 自动完成，Playwright 侧同样自动（配置自己建库并预置所需账号）。机检：`tests/acceptance/test_datastores_redis_slots.py`（两次取号必不同、释放后同号可再占）。**此前按 `pid % 6` 取号**：两个并发运行只要 pid 同余就共用同一个 DB（1/6 概率），Redis 承载去重键与队列 ⇒ 隔离在这条路径上是漏的，症状与上一条完全相同。
  - ❌ 共用 dev 库、再靠"跑前清残留"维持隔离 —— 残留来自被中断的运行与 dev 服务，清不干净就退化成随机红
  - ❌ 让使用者每次跑前手动 `export DATABASE_URL/REDIS_URL`（隔离会变成"记得设才有"，等于没有）
  - 开销实测 0.76s/轮（建库 0.10 + 迁移 0.65），相对验收总时长的量级可忽略
  - **清理要有界重试两类瞬时竞态**：`DROP DATABASE ... WITH (FORCE)` 在非超级用户角色（无 `pg_signal_backend`）撞上目标库的 autovacuum 等后台进程时会被拒（`InsufficientPrivilegeError`）——`_drop_database` 重试 4 次后仍失败才抛（`tests/acceptance/datastores.py`，2026-10-09 实测一次 teardown 偶发）；真实栈**存活期间**主动 purge 的用例（`test_recovery` 的「清理不留残留」）在 run_record/conversation 两条 DELETE 之间可能撞并发写入 ⇒ 整个清理事务重试（`purge_tenant`，`tests/acceptance/im_gateway/environment.py`）。
- **验收栈的「可注入节拍」一律注入小值，且测试里镜像这些节拍的常量必须读注入值**：轮询/扫描/退避这类间隔在验收里直接变成墙钟，而它们**不改变被测语义**（断言的是「最终发生」与「退避按几何级数增长」，不是节拍的绝对长度）。生产默认 → 注入值：`WORKER_POLL_INTERVAL_SEC` 5→1、`SCHEDULER_POLL_INTERVAL_SEC` 10→2、`TASK_DEADLINE_SWEEP_INTERVAL_SEC` 30→2、`DELIVERY_POLL_INTERVAL_SEC` 5→1、`DELIVERY_BACKOFF_BASE_SEC` 5→1（或 2）。三个栈各自在 `environment.py` 的 `base_env` 注入（`tests/acceptance/{dfx,task_schedule,im_gateway}/`）。
  - ✅ 测试侧常量**从注入值派生**：`DEADLINE_SWEEP_SEC = TASK_DEADLINE_SWEEP_INTERVAL_SEC`（`dfx/test_dfx_recovery.py`）、`SCHEDULER_POLL_SEC = SCHEDULER_POLL_INTERVAL_SEC`（`dfx/test_dfx_routing.py`）、窗口写 `DELIVERY_BACKOFF_BASE_SEC * 2**index`（`dfx/test_dfx_delivery.py`）
  - ❌ 在测试里**写死生产默认**（如 `SCHEDULER_POLL_SEC = 10`）：注入生效后**断言窗口与实际节拍静默脱节** —— 测试仍绿，但已不代表任何事
  - ❌ 无差别地压**租约**：`TASK_LEASE_SEC` 须显著大于心跳间隔（`dfx/environment.py:61-66` 的论证：压小会让「执行中不失约」不再可稳定观测，一次调度延误就把产品的**正确工作**误判成 heartbeat 失效）
  - 实测（2026-10-02，三笔 `91e5275`/`29f9c39`/`08af988`）：全量 `tests/acceptance` **1064s → 780s（−26.6%）**，单项最大 `im_gateway::test_b127` 81s → 16s
- **「常量值」与「行为规律」分层验证**：一条断言若为「等真实时间」而变得极慢（如 98s 的退避耗尽），拆成两层 —— E2E 注入小参数验证**规律**（几何增长、耗尽后写审计且不写已送达），**生产默认值**由毫秒级单测钉住（`tests/agent_worker/test_delivery_backoff_default.py` 钉 `delivery_backoff_base_sec == 5` 且 env 覆盖生效 —— 后者同时证明 E2E 的注入链路真的被读到）。
  - 前提是**该常量可配置**：产品侧这类时间常量应做成设置项而非模块常量（`BACKOFF_BASE_SEC` → `delivery_backoff_base_sec`，**默认值不变 ⇒ 生产行为不变**）。改名前先 grep 全仓引用，并同步**按名字引用它的 spec 约定**（`worker/harness-worker.md` 就按名字引用了它）。
  - ✅ 分层后两侧证明的事实集合不变：E2E 仍验「与生产公式一致」的形状，单测验常量取值。先例：14-dfx-acceptance 归档文档的「边界登记」明确「**调参不改公式不视为违规**，本用例钉的是形状不是常量取值」
  - ❌ 让「生产默认是 X」这条**常量**只有一条 98 秒的 E2E 覆盖 —— 慢得没人愿意跑，且一改参数就作废
- **验收耗时的优化必须由 `--durations` 归因驱动**，不得从「哪一段看起来慢」推瓶颈。实测反例（2026-10-02）：先假设「16 个 dfx 模块各起一套 live stack」与「worker 轮询 5s 是元凶」，**两个都错** —— `setup`（模块级栈启动）只占 **6.7%**，而前 5 条用例占 **52%**，榜首是**等退避窗口**的用例（`--durations` 把 setup/call/teardown 分开列，栈启动成本因此单独可见）。与既有纪律同源：「单跑通过、整跑偶发失败先怀疑环境残留，复跑再下结论，不要直接改实现」。
- 外部依赖（模型/LLM 端点、第三方 API）用真实本地探针服务承载：`tests/e2e/openai_probe_app.py`（真实 HTTP 健康响应）与 Console/Vite 并列写入 `webServer` 数组；禁止在 E2E 中伪造外部响应。
- **流式超时的「有界失败」用例必须注入 `STREAM_TIMEOUT_SEC`**：`RuntimeClient` 的流式读超时由**独立常量**决定——`create_run` 显式传 `httpx.Timeout(STREAM_TIMEOUT_SEC, connect=REQUEST_TIMEOUT_SEC)`，构造函数的 `timeout_sec` 只作用于非流式调用（`apps/im-gateway/src/muad_im_gateway/application/runtime_client.py`）。常量默认 300s，用例须 `monkeypatch` 成小值才能驱动「上游挂起 → 有界失败」的真实路径（`tests/gateway/test_runtime_client.py`）。
- **每个需求收尾必须有 `tests/<domain>_inventory.py` 闭合清单**：以**真实盘面**为输入做交叉核对，而不是自查断言——任务文档的覆盖表/契约表/Evidence 表、`.acceptance-manifest.json`、`spec-context.yml` 里的 required 规则，以及 E2E 套件与场景名是否**真的在盘**。口径（4 例：`tests/console_auth_inventory.py`、`tests/overview_dashboard_inventory.py`、`tests/audit_observability_inventory.py`、`tests/dfx_inventory.py`）：
  - 覆盖表每行（含 RULE 规则行）唯一负责人且终态，**且不豁免收口任务自身**——只豁免它会让「收口任务自己永远停在进行中」静默通过（13-console-auth 归档前的复发点）；manifest 与覆盖表同 ID / 同 owner / 同命令，**并比对 `level`/`boundary`/`cwd`**。
  - 终态场景与规则行在该 owner 的 Evidence 小节里登记且状态一致；**非 manual 行必须有同 ID 的证据表行**——runner 自动写的 `- <ID>: <status> — …` 条目**不能替代**（它会让「整行缺失」被自动条目掩盖；只有 manual 行接受条目形态）。
  - **契约表要为该任务 `Acceptance-Refs` 里的每个 ID 都留一行**：只断言「行是否终态」查不出**整行被删**（行没了就没有非终态可查，静默通过）。
  - 证据表不得残留占位行（`编码期填写`/`TBD` 一类）；`-k`/`-g` 令牌必须在真实用例名/真实 spec 文件里命中。
  - **结构性 RED + 扰动取证**：清单文件缺失时登记的 argv 必须失败（否则「先写用例后建清单」是空话）；至少扰动 4 类——改状态、删证据行、伪造用例名或命令、改 manifest 字段——每类须变红且消息指名条目，**逐字节还原后复跑全绿**。
  - **路径双写 live→archived**：`_dir()` 先试 `.code-flow/tasks/<日期>/<需求>`，不存在再取 `archived/`——硬编码 live 路径会让归档后整个验收套件变红（09/11/13 均踩过）。
  - **扰动锚点不得假设行处于某个非终态**：全量终验后所有行都是 `verified`，按 `e2e_deferred` 之类硬编码状态做锚点会让扰动用例在终态盘面上自红；应读取当前终态、改为非终态（如 `planned`），断言变红且指名条目、逐字节还原后复绿（2026-10-09 实例：`tests/async_tool_runtime_inventory.py::test_perturbation_changed_status_turns_terminal_check_red`）。
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
- **强杀/回收类用例的等待预算要给足整跑余量**：单跑 6–8s 能过的窗口在全量套件负载下会被拉长——15s 的 Reaper 回收等待与 5s 的强杀窗口都曾偶发不足（2026-10-09：`test_multipod_recovery.py::test_s04_e07` 放宽到 60s、`dfx/test_dfx_stateless.py::test_s12` 探针延迟 5s→15s）；只放大等待预算，断言不变。
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
- **测试文件的导入名必须唯一，否则全量 pytest 连收集都过不去**：两个**都没有** `__init__.py` 的目录里出现同名 `test_*.py` 时，pytest 用 basename 当模块名，`pytest tests/` 在收集阶段直接中断（`import file mismatch: imported module 'X' has this __file__ attribute: …/A/X.py`），而**单文件跑全绿**——报错里那句「remove `__pycache__`」是误导，真因是包标记缺失。仓内口径是**给其中一个目录补空的 `__init__.py`**（`tests/acceptance/{runtime,dfx,overview,task_schedule,audit_observability,console_auth_flow}/` 早有先例），改名会牵动任务文档 Acceptance Coverage/Contract 里已登记的测试路径与已有证据。
  - ✅ 定名之前先 `find tests -name "<basename>.py"`；撞名就补包标记，或改一个更具体的名字
  - ❌ 同一需求里按场景层各建一个 `test_<需求名>.py`（本次实例：`tests/gateway/test_execution_progress.py` 与 `tests/acceptance/im_gateway/test_execution_progress.py` 同名 ⇒ `pytest tests/` 一条都跑不了，而 `pytest tests/gateway` 与 acceptance 单跑都绿）
  - 机检：`tests/test_collection_layout.py::test_no_two_test_modules_share_an_import_name`（按 pytest 的 prepend 规则推导入名：目录链上有 `__init__.py` 的算包段，否则该目录名即模块名的起点）

## Avoid

- 违反上述任一规则的实现必须修复；与此 Spec 冲突的文档以本 Spec 与 `docs/` V1.4 为准。
