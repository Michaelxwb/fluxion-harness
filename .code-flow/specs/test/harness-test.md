---
id: harness-test
description: 测试分层、验收隔离与 CI
stages:
- design
- plan
- code
- review
enforcement: required
verifiers:
- rule: RULE-test-001
  type: command
  # 全量 acceptance + 前端 build + Playwright 是需求级终验，不在每个写测试的 code TASK
  # finish 中重跑；否则任何 tests/** 改动都会拉起全场 E2E。
  stage: review
  config:
    argv:
    - bash
    - -lc
    - uv run pytest -q -m "not capacity_load" tests/acceptance && npm --prefix
      apps/console-platform/frontend run build && npm --prefix e2e test
    cwd: .
    # 链 = 非容量 acceptance + 前端 build + Playwright e2e。capacity_load 是手动容量边界，
    # 不进 Done Gate。曾因共享库上的孤儿验收栈（Worker 抢任务）把耗时放大到 1200s 以上，
    # 清孤儿后 2400s 实测通过；预算只给这条终验链。
    timeout: 2400
---

# harness-test

## Rules

- [RULE-test-001] 跨 API/DB/Runtime/Browser 的关键流程必须 E2E 且明确“不得 mock 的真实边界”（真实 PostgreSQL、真实 Redis 行为、真实 HTTP、真实浏览器渲染）；单元测试覆盖纯逻辑与状态机，契约测试覆盖枚举/错误码/迁移一致性。

## Conventions

**测试文件与收集**：

- **测试文件的导入名必须唯一**：两个都没有 `__init__.py` 的目录里出现同名 `test_*.py` 时，pytest 用 basename 当模块名，`pytest tests/` 在收集阶段直接中断（`import file mismatch`），而单文件跑全绿——报错里那句「remove `__pycache__`」是误导。仓内口径是给其中一个目录补空的 `__init__.py`（`tests/acceptance/{runtime,dfx,overview,task_schedule,audit_observability,console_auth_flow}/` 有先例），改名会牵动任务文档已登记的测试路径。机检：`tests/test_collection_layout.py::test_no_two_test_modules_share_an_import_name`。
- **失败即时可见**：根 `tests/conftest.py` 的 `pytest_runtest_logreport` 在失败/出错时立刻打印 `FAILED <nodeid> [phase]`，可选追加到 `PYTEST_FAILURE_LOG`；即使整轮被 timeout/kill，日志里也有可定位的用例名。两个 heavy pytest 命令加 `--durations=30`。
- **流式超时的「有界失败」用例必须注入 `STREAM_TIMEOUT_SEC`**：`RuntimeClient.create_run` 显式传 `httpx.Timeout(STREAM_TIMEOUT_SEC, connect=REQUEST_TIMEOUT_SEC)`，构造函数的 `timeout_sec` 只作用于非流式调用（`apps/im-gateway/src/muad_im_gateway/application/runtime_client.py`）；用例须 monkeypatch 成小值才能驱动「上游挂起 → 有界失败」（`tests/gateway/test_runtime_client.py`）。

**验收隔离（不可共用 dev 库）**：

- **每轮独占一个空库 + 一个独立 Redis DB，且自动生效**：任务队列的 claim 与投递选取**都不带租户谓词**（`worker/claimer.py` 的 `_claimable_conditions`、`delivery/service.py` 的 `_query_pending_tenants`），各栈的**出站渠道端点是栈级 env**（`CHANNEL_PROBE_URL`）。别的套件或 dev 服务留下的可领取/待投递行会被本栈领走并按本栈端点投递，表现为「我的探针里出现别人的投递」，断言随机红且**单跑绿、串跑红**。
  - ✅ 每轮建 `muad_acc_<uuid>` + `alembic upgrade head`（临时 ini + `migrations/db_migrate.py -c`），Redis 在 10–15 号里 `SET NX EX` 原子占位（收尾归还、2h TTL 兜底、全占满显式失败）；pytest 由 `tests/acceptance/conftest.py` 自动完成，Playwright 由域配置自己建库。
  - ❌ 共用 dev 库再靠「跑前清残留」维持隔离；❌ 让使用者手动 `export DATABASE_URL/REDIS_URL`。
  - 清理要有界重试两类瞬时竞态：`DROP DATABASE ... WITH (FORCE)` 撞 autovacuum 的 `InsufficientPrivilegeError` 重试 4 次；真实栈存活期 purge 撞并发写入时整个清理事务重试（`tests/acceptance/datastores.py`、`tests/acceptance/im_gateway/environment.py`）。
- **验收栈的「可注入节拍」一律注入小值，测试侧常量必须读注入值**：轮询/扫描/退避不改变被测语义，只改变墙钟。生产默认 → 注入值：`WORKER_POLL_INTERVAL_SEC` 5→1、`SCHEDULER_POLL_INTERVAL_SEC` 10→2、`TASK_DEADLINE_SWEEP_INTERVAL_SEC` 30→2、`DELIVERY_POLL_INTERVAL_SEC` 5→1、`DELIVERY_BACKOFF_BASE_SEC` 5→1（或 2）。✅ 测试常量从注入值派生（`DEADLINE_SWEEP_SEC = TASK_DEADLINE_SWEEP_INTERVAL_SEC`）；❌ 写死生产默认（注入生效后断言与实际节拍静默脱节）；❌ 无差别压 `TASK_LEASE_SEC`（须显著大于心跳间隔，`dfx/environment.py`）。
- **「常量值」与「行为规律」分层验证**：等真实时间变慢的断言拆两层——E2E 注入小参数验规律（几何增长、耗尽后写审计），生产默认值由毫秒级单测钉住（`tests/test_platform_settings_schema.py::test_other_groups_keep_designed_defaults`）。前提是常量可配置（设置项而非模块常量）；✅ 两侧证明的事实集合不变；❌ 让「生产默认是 X」只有一条 98 秒 E2E 覆盖。
- **验收耗时优化必须由 `--durations` 归因驱动**：`setup` 与 `call` 分开看，不要从「哪段看起来慢」推瓶颈；实测反例是「栈启动慢」与「轮询 5s」都错，榜首是等退避窗口的用例。
- **运行前 Redis claim 预清理**：`make acceptance` 先跑 `scripts/clean_acceptance_claims.py`；仅当没有活跃 pytest/Playwright 进程时清 10–15 号 claim。`make acceptance-clean` 是显式 `--force` 入口；异常中断仍由 7200s TTL 兜底。
- **容量压测分层**：`tests/acceptance/capacity/test_capacity.py` 标记 `integration + capacity_load`，默认 `make acceptance` / `make test` 用 `-m "not capacity_load"` 排除；显式入口 `make capacity-smoke`（100 用户）、`make capacity-load`（200）、`make capacity-soak`（400，需 `MUAD_CAPACITY_SOAK=1`）。SLO 判定走 `tests/capacity/slo.py`，状态/速率由 `tests/capacity/metrics.py` 采集。1000 用户 × 8 轮是 manual/staging 未验证边界，不进 Done Gate。

**Runtime 真实环境与收口**：

- **Runtime 验收真实环境基建**（`tests/acceptance/runtime/`）：module-scoped live stack——Console/Runtime 真实 uvicorn、LLM/MCP 本地 HTTP 探针、数据落真实 PostgreSQL/Redis/Artifact Store；禁止 `dependency_overrides`/mock 业务服务。跨 Pod/崩溃用真实子进程（SIGKILL 后由另一实例 Reaper 回收）；进程内服务线程与 pytest-asyncio 并存时清理 engine `lru_cache`，async 工具走独立线程（禁止 `asyncio.run` 污染主循环）。
- **强杀/回收类用例的等待预算要给足整跑余量**：单跑 6–8s 能过的窗口在全量负载下会拉长；只放大等待预算，断言不变（实例：Reaper 回收 15s→60s、探针延迟 5s→15s）。
- **验收命令由 acceptance manifest runner 统一复验并写证据**：相同 argv/cwd/timeout 复用；测试未收集、未执行或失败均视为 FAIL。
- **改任务文档后重生成 manifest 会保留证据、把契约变化标 stale**（0.8 起）：`cf_acceptance_manifest.py --task-file … --output …` 保留已有 `evidence`/`runs`；契约变化置 `stale` 并列入 `relock.stale`。补证据可按 owner 回填：`cf_acceptance_runner.py --manifest <需求目录>/.acceptance-manifest.json --root "$PWD" --write-evidence --owner TASK-0XX`（无需 active marker；不带 `--include-e2e` 时 E2E 行保持 deferred）。
- **每个需求收尾必须有 `tests/<domain>_inventory.py` 闭合清单**：以**真实盘面**为输入交叉核对，而不是自查断言——任务文档覆盖表/契约表/Evidence、`.acceptance-manifest.json`、`spec-context.yml` 的 required 规则、E2E 套件与场景名是否真的在盘。现网 11 个（console_auth/overview_dashboard/audit_observability/dfx/agent_memory/async_tool_runtime/context_compaction/memory_redesign/platform_settings/run_event_details 等）。
  - 覆盖表每行（含 RULE 规则行）唯一负责人且终态，**不豁免收口任务自身**；manifest 与覆盖表同 ID/owner/命令，并比对 `level`/`boundary`/`cwd`。
  - 终态场景与规则行在该 owner 的 Evidence 里登记且状态一致；**非 manual 行必须有同 ID 的证据表行**——runner 自动写条目不能替代（会掩盖整行缺失）。
  - 契约表要为该任务 `Acceptance-Refs` 的每个 ID 留一行（只断言「行是否终态」查不出整行被删）；证据表不得残留占位行；`-k`/`-g` 令牌必须真实命中。
  - **结构性 RED + 扰动取证**：清单缺失时 argv 必须失败；至少扰动 4 类（改状态/删证据行/伪造用例名或命令/改 manifest 字段），每类变红且指名条目，逐字节还原后复绿。扰动锚点读当前终态再改为非终态，不假设行处于 `e2e_deferred`。
  - **路径双写 live→archived**：`_dir()` 先试 live，再取 `archived/`；硬编码 live 路径会让归档后整套验收变红。
  - **改动 `apps/`、`packages/` 模块级常量（大写赋值）的任务必须重生成 `docs/configuration-inventory.csv`**：`python-constant` 行由 AST 逐行比对，行号位移或增删都会红；重算后跑 `tests/test_configuration_inventory.py`。

**CI**：

- **CI-first 验收**：全量 acceptance / 单测以 CI 为准，本地只跑靶向套件；已知失败登记在 `docs/operations/e2e-known-issues.md`。
- **后端 CI 并行分片**：`backend-static` 只跑 compile/i18n/error-message/lint/typecheck（无 DB）；`backend-tests` 按目录切 `fast`/`runtime`/`console`/`worker` 四个 matrix job，**每个 job 自带 PG/Redis service**，互不共享库与 Redis 号位。`acceptance` 按模块切 `dfx`/`gateway`/`im_gateway`/`runtime`/`capacity`/`task_schedule`/`rest` 七片，各自一套 PG/Redis 与临时库——起真实 Worker/Runtime 的模块串在一个 job 会污染后续模块。CI 预建 `.data/artifacts` 与 `.data/skill-cache`。分片是人工清单：新增顶层测试目录（含 `tests/acceptance` 子目录）必须同步 `.github/workflows/check.yml`，否则静默漏跑；四个单测分片收集条数应互不重叠并等于拆分前的收集数。

**验收反模式（Anti-Patterns）**：

- ❌ 恒真断言：`assert reaped >= 0`、对可能为空集合 `all(...)`、`exclude={"api_key"}` 后再断言无 `api_key`——断言必须能真实失败。
- ❌ 命令与场景不符：`-k` 未命中任何测试（退出码 5）却标记 verified；测试名/文件与 Acceptance Contract 声明不一致。
- ❌ 验收层级注水：把 service/DB 级测试写成 E2E，或边界文案（真实 Gateway/进程/SSE）与测试实际行为不符；用 jsdom/组件单测冒充 E2E。
- ❌ 空转用例：测试名说「跨 host 跳转拒绝」却请求 `/healthz`、`follow_redirects=False` 从未触发跳转。
- ❌ **多输入断言的腿被合并**：断言「改配置/授权只影响新 Run」必须一次只改一个输入，并断言其余腿对应列不变。✅ 逐腿改 `agent.instructions` 与 `model.params_json` 各断言一次；❌ 同时改两处后只断言 hash 变了。
- ❌ 把验收/Done Gate 输出接进会提前关闭的管道（典型 `| head`）：SIGPIPE 打断 pytest 收尾，留下 uvicorn/`muad_*.main` 孤儿进程继续连同一个本地测试库，后续随机失败。用 `> file` 或 `tail`，每次运行前确认无残留进程。**本条无机检，靠运行前自查与评审。**
- ❌ 只断言「有变更时的主路径」：命令副作用边界（空库 CLEAR 也要撤销 pending 提议）、同响应多字段必须同快照（`total`/`items` 同一条 SQL）、删除后必须经界面入口可达，三类各写一条可独立失败的用例。
- ❌ 把「单跑绿、整跑红」一律当测试竞态：先分类——测试时序假设改为确定性前提（放大在飞窗口/等待终态），**产品行为变化必须修产品**，不得用等待绕过去。
- ❌ 修复类任务只贴 GREEN：必须带负向对照（移除修复或注入旧行为 → 必须红）的一次实证。

## Avoid

- 违反上述任一规则的实现必须修复；与此 Spec 冲突的文档以本 Spec 与 `docs/` V1.4 为准。
