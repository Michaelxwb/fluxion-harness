# 验收数据存储隔离：每个运行独占空库 + 独立 Redis DB

- **状态**: 已落地（2026-10-02）
- **性质**: **存档记录**，不是走完 cf-task 流程的需求。它是在 `16-agent-memory` 的需求级终验
  （`verify-e2e`）中被发现并当场修掉的，事后按「轻量存档」口径补记 —— 没有 spec-context.yml、
  没有 manifest、没有跑 plan/code/review 门禁，**也没有伪造事后证据**。规则本体落在
  `.code-flow/specs/test/harness-test.md`（含机检锚点），此处只记「为什么有这几个提交」。
- **相关提交**: 见文末清单

## 为什么要做（根因）

验收各域共用一份 dev 库时，**跨套件互相污染**，症状是「单跑绿、串跑红」且失败点每次不同。
2026-10-02 实测：`verify-e2e` 的顺序执行下 `task_schedule` 的投递用例**必红**，单独跑全绿。

三条机制叠加才成立，缺一不会：

1. **任务领取不带租户谓词** —— Worker 是无状态通用工作池，任务行自带 `tenant_id`，
   `_claimable_conditions` 不做租户过滤（`apps/agent-worker/.../worker/claimer.py:20-27`）。
   别的套件（或 dev 服务）留下的可领取行会被**本栈**的 Worker 领走。
2. **投递选取同样不带租户谓词**（`applications/.../delivery/service.py:120-139`）。
3. **出站渠道端点是栈级 env** —— `CHANNEL_PROBE_URL` 让 Gateway 换用 HTTP 探针适配器
   （`apps/im-gateway/.../im_gateway/main.py:35-39`）。

于是别的套件投递的**内容**会落到**我的**探针里：`assert len(deliveries) == 1` 这类全局计数断言
随机失败。注意这不是「谁写脏了数据」的卫生问题 —— 是**结构性**的，只要共库就会出现。

## 做法

**每个运行独占一个空库 + 一个独立 Redis DB，且自动生效（不需要任何显式指定）。**

- 建库 / 迁移 / 清理的**唯一实现**在 `tests/acceptance/datastores.py`：
  现场生成只含目标 DSN 的临时 alembic ini，用 `migrations/db_migrate.py -c` 迁到 head
  （与 CI 同口径：迁移只认 ini、不读环境变量）。pytest 与 Playwright 两侧都调它，避免两份逻辑漂移。
- **pytest 侧**：`tests/acceptance/conftest.py` 的 session autouse fixture 建 `muad_acc_<uuid>`、
  迁移、**同时改写 `os.environ`**（只注入子进程会让 seed 写进旧库、服务读新库 —— 每个域都会红），
  收尾 DROP（`WITH (FORCE)`）。
- **Playwright 侧**：`e2e/support/isolated-datastores{,-teardown}.ts` 调同一个 Python CLI，
  并把结果冻结进 `process.env`（配置会在**每个 worker**里被重新求值，不冻结就会一轮建出 N 个库）；
  10 份域配置各加 `globalTeardown` + `useIsolatedDatastores()`。
- **逃生阀**：`MUAD_ACCEPTANCE_SHARED_DB=1` 退回共享库，仅用于对比排查。
- **刻意不做**：不「跑前清残留」维持隔离 —— 残留来自被中断的运行与 dev 服务，清不干净就退化成随机红；
  也不要求使用者每次手动 `export DATABASE_URL/REDIS_URL`（那等于「记得设才有」）。
- 开销实测 **0.76s/轮**（建库 0.10 + 迁移 0.65），相对验收总时长可忽略。

### 两个实现上的坑（都踩过）

- **不能用 `asyncio.run`** 跑建库/删库：它会把主线程事件循环置空，本会话其后所有异步套件集体
  `no current event loop`（实测 95 failed）。改为在**独立线程**里开事件循环。
- **必须同时改 `os.environ`**，理由见上。

## 后续补强（同一机制的第二轮）

2026-10-02 复查发现 Redis 号位仍按 `os.getpid() % 6` 取：两个并发运行只要 pid 同余就**共用同一个
DB**（1/6 概率），Redis 承载去重键与队列 ⇒ 隔离在这条路径上是**漏的**，症状与上面完全相同。
已改为 `SET NX EX` **原子占位**（收尾归还、2h TTL 兜底异常中断、号位占满则显式失败而非静默退回共享），
机检 `tests/acceptance/test_datastores_redis_slots.py`。

## 提交

| 提交 | 内容 |
|---|---|
| `201b948` | `test(acceptance)`: pytest 侧每轮独占空库 + 独立 Redis DB |
| `0c1778d` | `test(e2e)`: Playwright 侧同样独占，复用验收建库逻辑（同一个 `datastores.py`） |
| `c6bfc4c` | `docs(spec)`: `harness-test.md` 增约定（含 ✅/❌ 口径） |
| `8df86d6` | `docs`: 把「验收怎么跑」放进每会话可见处（`CLAUDE.md` + `Makefile`） |
| `cc2810d` | `fix(cf-task)`: `verify-e2e` 的证据被逐条覆盖，只剩最后一条规则落盘（同一轮排查出的工具缺陷） |
| `67346d5` | `test(e2e)`: 两个域套件的断言对齐真实实现（排查过程中暴露的既有漂移） |

## 验证

- 全量验收 `uv run pytest -q tests/acceptance` 全绿（含本机制自带的 session fixture）。
- Playwright 各域在独立库 + 独立 Redis 下实跑通过（含 task_schedule 的投递用例 —— 即改动前
  顺序执行必红的那条）。
- 补记时的复跑（2026-10-02）：`make acceptance` → **265 passed in 1064.07s**；
  `make acceptance-e2e DOMAIN=mcp` → **6 passed**（覆盖 Playwright 侧经 CLI 的占号/释放链路）。

## 耗时现状（供后续优化参考）

`tests/acceptance/` 单跑 **265 条约 17 分 44 秒**，且**耗时集中**：前 71 条（按 pytest 收集序
= `audit_observability` 10 + `console_auth_flow` 33 + `dfx` 前 29）用了 9 分 15 秒（约 7.8 秒/条），
其余 194 条约 8 分半（约 2.6 秒/条）。

- 为什么「子集」比全量树里其余部分慢得多：这个子树按设计起**真实进程**（Console/Runtime×2/
  Worker/Gateway/探针）+ 真实 PG/Redis，并用**轮询**等待真实链路完成；全量树里其余 ~1400 条是
  毫秒级单元/契约测试（`tests/frontend/` 250 条仅 0.18 秒）。
- 等待时长由**产品的轮询间隔**决定（`packages/common/src/muad_common/settings.py:41-49`：
  worker 5s / scheduler 10s / delivery 5s），但 Worker 有 Redis pub/sub 唤醒
  （`infrastructure/wakeup_hint.py` 的 `task:wakeup`），故 5s 是**兜底周期不是常见延迟**。
- 慢区的 16 个 `dfx` 测试模块是嫌疑重点（模块级 live stack 每模块一套），**尚待 `--durations`
  实测确认**，见下条「遗留」。

## 遗留

- 号位上限 6（Redis 默认 16 个 DB，10–15 留给验收）：同机并发超过 6 个验收会**显式失败**而不是
  静默降级，这是有意取舍。真要更多并发需要调 Redis `databases` 配置。
- 本记录不含 cf-task 门禁轨迹 —— 如需合规轨迹，须另建需求目录走完整流程（属另一件事）。
