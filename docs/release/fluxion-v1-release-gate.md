# Fluxion V1 Release Gate

- 核验日期：2026-08-24（重新生成，TASK-108 闭环后）
- 核验范围：Phase 01-13 已实现的 Runtime、Console 与 Chat
- 结论：**PASS（本地）**。Console/Chat 浏览器真实 HTTP 集成已闭环（宿主 Chrome S-P13-05/S-P13-06/E-P13-03 浏览器 E2E GREEN）；剩余标记 `ENV REQUIRED` 的 live smoke（S-P13-07）、多 Pod 与 Canary 验证须在发布环境完成

## Gate Summary

| Gate | 本地结果 | Evidence |
|------|---------|----------|
| Runtime/Console NFR | PASS，8/8 | `python3 -m pytest backend/tests/benchmarks --benchmark-only` |
| 故障注入与恢复 | PASS，20/20 | Registry/Event/Audit/Secret/Model/Hook/Sandbox 测试集 |
| 架构依赖与 Schema compatibility | PASS，7/7 | Kernel/Runtime AST 依赖检查 + shared contract |
| Registry 双库 Contract | PASS，24/24 | 同一套 12 项 Contract 分别运行 SQLite/PostgreSQL（含 S-P13-04） |
| P0/P1 自动化率 | PASS，已完成 TASK 74/74（100%，含 TASK-107 的 B-C104/B-C107）；TASK-108 场景 10/11 verified（S-P13-07 待外部凭据） | `backend/tests/unit/test_release_gate.py` 动态解析已完成 TASK |
| Backend 全量回归 | PASS，163 passed / 1 skipped | `python3 -m pytest backend/tests -q`（skip=S-P13-07 live smoke，受 `FLUXION_LIVE_MODEL_SMOKE` 门控） |
| Frontend 回归与构建 | PASS（production HTTP client + InMemory-import 守卫） | Console/Chat typecheck、lint、build；`node frontend/scripts/check-no-inmemory.mjs` |
| React→HTTP→FastAPI→SQLite | **PASS** | 宿主 Chrome E2E：`console-real-http.spec.ts`（S-P13-05）、`agent-golden-path.spec.ts`（S-P13-06）、`agent-error-path.spec.ts`（E-P13-03） |

## Release Blocker（已解除，TASK-108 闭环）

- 原 BLOCKED：Console 生产入口直接创建 `InMemoryConsoleApi`，所有管理页面默认读取内存 seed。→ 已替换为共享 production HTTP client，`main.tsx` 走真实 `fetch`；InMemory 仅保留为显式注入的测试 fixture，`frontend/scripts/check-no-inmemory.mjs` 在 production build 阶段静态禁止 import InMemory API（本轮 PASS）。
- 原 BLOCKED：Console `ConsoleApi` 与 FastAPI 契约未对齐，Binding list、Credential、Run/Trace、Audit 和 P1 管理视图缺少对应 HTTP surface。→ 已补齐 read-side surface 并接入统一 Response Factory/envelope/X-Request-ID/RequestContext；Eval 视图经 `api/eval.py` 接真实 HTTP（`listP1View("eval")` → `GET /api/v1/eval/runs`）。全部 P1 视图已接线：`users_channels`→`/api/v1/platform-users`、`plugin_policy`→`/api/v1/policies`（tenant 隔离 Policy 资源）、`capabilities`→`/api/v1/capabilities`（dev bundle 注入 `runtime.plugin_summaries` 派生的 capability descriptor）、`runtime_status`→`/api/v1/runtime-status`（只读运行时身份/健康，不管理 Pod），B5 闭环（`test_p1_views_api.py` 5 passed、`httpConsoleApi.test.ts` 5 passed）。
- 原 BLOCKED：Chat 使用 HTTP Client 但 Vite 无 `/api` proxy、CLI 无 Channel API 本地装配。→ `fluxion serve --dev` 组合 Console API、Channel API、Runtime、SQLite 与已构建 Console/Chat 静态资源，提供一致 `/console`、`/chat/#/<token>`、`/api` 路由。
- 原 BLOCKED：仅 backend ASGI 进程内 golden path，不覆盖 React/真实网络/浏览器。→ 宿主 Chrome 浏览器 E2E（S-P13-05/S-P13-06/E-P13-03）覆盖 Chromium → real fetch/SSE → FastAPI → SQLite → Runtime → HTTP Model → MCP → Trace，全部 GREEN。
- 发布边界：S-P13-07 live smoke 需真实 OpenAI-compatible 外部凭据，保持 planned 不伪造；获取凭据后 `FLUXION_LIVE_MODEL_SMOKE=1 python3 -m pytest backend/tests/e2e/test_live_agent_smoke.py -k S_P13_07 -s` 补 GREEN evidence。

## Performance Evidence

统一复验命令：

```bash
PATH="$PWD/.venv/bin:$PATH" python3 -m pytest backend/tests/benchmarks --benchmark-only
```

测试内部以完整 round 延迟样本断言 P95/P99；下表的观测值是本次 pytest-benchmark mean，不替代 percentile 断言。

| 场景 | 预算 | 本次 mean | 结果 |
|------|------|-----------|------|
| B-R04 Resolver L1 | P95 <= 5ms | 0.0008ms | PASS |
| B-R05 Hook Framework | P95 <= 10ms | 0.873ms | PASS |
| B-R06 Runtime Framework | P95 <= 50ms，P99 <= 100ms | 0.077ms | PASS |
| B-R07 Snapshot Builder | P95 <= 20ms | 0.0028ms | PASS |
| B-C104 Resource List/Detail | P95 <= 300ms | 13.74ms | PASS |
| B-C105 Publish API | P95 <= 500ms | 17.90ms | PASS |
| B-C106 Bind/Chat Framework | P95 <= 300/200ms | 7.11ms | PASS |
| B-C107 Trace Query | P95 <= 500ms | 0.042ms | PASS |

生产 Canary 不使用本地数据替代：需持续至少 30 分钟，5 分钟 API error rate <0.5%；error rate >=1% 或 P95 超目标两倍持续 10 分钟时回滚。

### Phase-13 产品闭环 Benchmark 与浏览器证据（TASK-108）

```bash
PATH="$PWD/.venv/bin:$PATH" python3 -m pytest \
  backend/tests/benchmarks/test_agent_product_benchmark.py \
  backend/tests/benchmarks/test_runtime_overhead.py -q
pnpm exec playwright test
node frontend/scripts/check-no-inmemory.mjs
```

| 场景 | 断言 | 结果 |
|------|------|------|
| B-P13-01 Runtime/MCP pool/Chat framework | P95/P99 预算内（排除模型与外部 Tool 耗时） | PASS（4 passed；注：复验初期 15 核被 `node -e while(true){}` 占满 load≈108 导致 P99 超预算，暂停 CPU burner 后通过，属环境噪声） |
| S-P13-05 浏览器 Console 真实 HTTP | Chromium → real fetch → FastAPI → SQLite，刷新后数据仍在，0 mock API | PASS（`frontend/e2e/console-real-http.spec.ts`） |
| S-P13-06 浏览器 Golden Path | 专属 Chat 链接 → 模型主动调用 MCP → 最终答案；Trace 精确版本与 policy_decision_id | PASS（`frontend/e2e/agent-golden-path.spec.ts`，宿主 Chrome `channel: "chrome"`） |
| E-P13-03 浏览器错误恢复 | 统一 API envelope、失效链接/依赖失败可恢复、不泄露堆栈 | PASS（`frontend/e2e/agent-error-path.spec.ts`） |
| InMemory-import 守卫 | production source 不得 import 任何 InMemory API | PASS（`check-no-inmemory.mjs`） |
| Eval API | `POST/GET /api/v1/eval/runs`、`GET /{run_id}`、`POST /api/v1/eval/runs:compare`，错误码 37_000-37_003 | PASS（`test_eval_api.py` 2 passed；`test_plugin_publish_validation.py` 2 passed） |
| P1 视图全接线（B5） | `users_channels`→platform-users、`plugin_policy`→policies（tenant 隔离）、`capabilities`→capability descriptors（dev bundle 注入）、`runtime_status`→只读身份/健康 | PASS（`test_p1_views_api.py` 5 passed；`httpConsoleApi.test.ts` 5 passed；dev bundle 端到端 curl 验证统一 envelope） |
| S-P13-07 live smoke | 真实 OpenAI-compatible Provider 模型 tool call + MCP + 最终回答 | **ENV REQUIRED**（无外部凭据，不伪造；`FLUXION_LIVE_MODEL_SMOKE=1 ... -k S_P13_07 -s`） |

## Fault And Recovery Evidence

```bash
PATH="$PWD/.venv/bin:$PATH" python3 -m pytest \
  backend/tests/integration/test_registry_degraded.py \
  backend/tests/integration/test_outbox.py \
  backend/tests/integration/test_audit_failure.py \
  backend/tests/integration/test_local_secret_store.py \
  backend/tests/e2e/test_model_provider.py \
  backend/tests/e2e/test_sandbox.py \
  backend/tests/integration/test_hooks.py -q
```

| 注入点 | 断言 |
|--------|------|
| Registry 不可用 | 仅在安全 stale 窗口降级；无缓存时明确失败 |
| Event 发布失败/模糊写 | Outbox 保持 pending 并可重试；revision ID 保证幂等恢复 |
| Audit 写入失败 | Publish、Audit、Outbox 同事务回滚，无高影响操作漏审计 |
| Secret 主密钥缺失/错误 | AES-256-GCM 解密 fail-closed，不返回明文 |
| Model/Hook 超时 | 有界 timeout/retry；按 fail-open/fail-closed 策略执行 |
| Sandbox 不可用 | untrusted Plugin 明确失败并产生 trace event |

恢复操作不修改 Pod 本地事实状态：恢复 Registry/Event 依赖后由 revision polling、Outbox retry 和下一次解析自动收敛；Resource 回滚只选择不可变历史版本。

## Contract And Architecture Evidence

```bash
PATH="$PWD/.venv/bin:$PATH" FLUXION_REQUIRE_POSTGRES_CONTRACT=1 \
  python3 scripts/run_registry_contract_tests.py
PATH="$PWD/.venv/bin:$PATH" python3 -m pytest \
  backend/tests/unit/test_kernel_boundaries.py \
  backend/tests/contract/test_shared_contracts.py -q
```

- SQLite/PostgreSQL 使用同一 RegistryStore Contract，Docker PostgreSQL 16 实测 24/24 通过（2026-08-24 复验，12 项 Contract × 双库，含 S-P13-04）。
- Kernel AST 门禁禁止依赖 API、Service、Registry 与具体 Plugin；Runtime 禁止依赖 Console API/Application Service。
- Console 发布的共享 Resource Contract 可被 Runtime Resolver 直接读取，Published 版本保持不可变。

## Runtime DFX

| DFX | Evidence | 结论 |
|-----|----------|------|
| DFX-01 Availability | Registry stale、Hook fail-open、Model/Sandbox 故障注入 | PASS |
| DFX-02 Reliability | Immutable Version、ExecutionSnapshot、Outbox 幂等恢复 | PASS |
| DFX-03 Scalability | Runtime 无 Pod 事实状态、tenant-aware cache；多 Pod 压测留在发布环境 | CODE PASS / ENV REQUIRED |
| DFX-04 Performance | B-R04/B-R05/B-R06/B-R07 benchmark | PASS |
| DFX-05 Security | tenant isolation、SecretRef、Hook trust/fail policy、Sandbox | PASS |
| DFX-06 Maintainability | Microkernel AST 依赖门禁 | PASS |
| DFX-07 Testability | SQLite/PostgreSQL 共用 Contract 与 unit/integration/E2E | PASS |
| DFX-08 Observability | ExecutionSnapshot、TraceRecord、tool/hook/model event | PASS |
| DFX-09 Deployability | Resource hot publish 与 Runtime 程序发布解耦；rolling drill 留在发布环境 | CODE PASS / ENV REQUIRED |
| DFX-10 Compatibility | shared Resource Contract 与 deprecated lifecycle 测试 | PASS |
| DFX-11 Recoverability | Registry stale、Outbox retry、Model retry/recovery | PASS |
| DFX-12 Operability | structlog JSON、统一 error taxonomy、request_id/trace_id | PASS |

## Console And Chat DFX

| DFX | Evidence | 结论 |
|-----|----------|------|
| DFX-CP-01 Availability | Console/Runtime 读取边界解耦；Registry degraded 测试 | PASS |
| DFX-CP-02 Scalability | Console、Chat、Runtime 独立入口/构建；环境扩容压测待 Canary | CODE PASS / ENV REQUIRED |
| DFX-CP-03 Security | 管理/对话路由隔离，Bind Code 单次/TTL/hash 测试 | PASS |
| DFX-CP-04 Maintainability | 同仓共享 Schema/Contract 与依赖门禁 | PASS |
| DFX-CP-05 Testability | 双库 Contract、Bind/Chat E2E、前后端自动化测试 | PASS |
| DFX-CP-06 Observability | bind/platform user/execution/request/trace 关联测试 | PASS |
| DFX-CP-07 Deployability | Console/Chat 独立 Vite 产物；镜像/Canary 演练留在发布环境 | CODE PASS / ENV REQUIRED |
| DFX-CP-08 Usability | Console 管理导航与 Chat 用户入口为独立应用 | PASS |

## Reproducible Full Gate

```bash
PATH="$PWD/.venv/bin:$PATH" ruff check backend/src backend/tests
PATH="$PWD/.venv/bin:$PATH" mypy backend/src
PATH="$PWD/.venv/bin:$PATH" python3 -m pytest backend/tests -q
PATH="$PWD/.venv/bin:$PATH" FLUXION_REQUIRE_POSTGRES_CONTRACT=1 python3 scripts/run_registry_contract_tests.py
pnpm --filter @fluxion/console test
pnpm --filter @fluxion/chat test
pnpm --filter @fluxion/console lint
pnpm --filter @fluxion/chat lint
pnpm --filter @fluxion/console typecheck
pnpm --filter @fluxion/chat typecheck
pnpm --filter @fluxion/console build
pnpm --filter @fluxion/chat build
node frontend/scripts/check-no-inmemory.mjs
pnpm exec playwright test
```

本轮复验结果：backend 163 passed / 1 skipped（skip=S-P13-07 live smoke，受 `FLUXION_LIVE_MODEL_SMOKE` 门控）、ruff/mypy clean、Store Contract（SQLite）12 passed、console/chat typecheck/lint/build 全绿、InMemory-import 守卫 PASS、宿主 Chrome 浏览器 E2E S-P13-05/S-P13-06/E-P13-03 GREEN。

只有所有本地 Gate 通过，并完成标记为 `ENV REQUIRED` 的 S-P13-07 live smoke、staging 多 Pod、独立部署与 Canary 验证后，才可进入生产全量发布。

---

## V1 第一阶段缺陷修复记录（2026-08-25）

针对「第一阶段开源无状态 Agent 项目」深度评审发现的缺陷，本轮落地以下修复：

| 缺陷 | 修复 | 证据 |
|------|------|------|
| 无 LICENSE / 开源治理 | 补 Apache-2.0 `LICENSE`、`CONTRIBUTING`、`CHANGELOG`、`SECURITY`、`CODE_OF_CONDUCT`、`.github/workflows`（backend + frontend + E2E CI） | 文件存在；CI 命令与 release gate 一致 |
| 无状态承诺被 InMemory 记忆架空 | 新增 `SQLSessionMemoryStore`（`session_memory` 表），L1/L2/summary 持久化到共享 Registry；删除 `local_durable_fact_count` 作弊桩 | `test_stateless_runtime.py` 用文件 SQLite 验证 Pod 重启后记忆仍在 |
| deploy 目录全空 | 补齐 `deploy/docker`（Dockerfile/docker-compose/entrypoint）、`deploy/helm/fluxion`、`deploy/README.md`、`.dockerignore` | 产物自洽，含环境变量桥接说明 |
| shared/contracts 空（语言无关契约缺） | 留待后续（OpenAPI 产物生成器） | — |
| Redis 配置通知是桩 | 新增 `redis_streams.py`（`RedisStreamsClient` + `RedisConfigEventSubscriber` + 工厂），依赖声明 `redis>=5.0` | `test_redis_streams.py` 4 passed |
| Alembic migration 缺失 | 落地 `alembic.ini` + `env.py` + 初始 migration（11 张表含 session_memory） | `test_migration.py` upgrade/downgrade 验证通过 |
| OpenTelemetry 零落地 | 新增 `observability/tracing.py`（TracerProvider + OTLP exporter），middleware 与 Runtime 创建 span 关联业务 trace_id | `test_tracing.py` 2 passed |
| 5 个文件超 500 行 | 拆分 console_app / api/console / sqlalchemy_store / runtime_app / mcp | 各文件 ≤ 500 行 |
| Web Chat 无流式渲染 | `OpenAICompatibleHTTPModelProvider.stream` + channel SSE token 转发 + 前端 `streamEvents` 逐 token 渲染 | `test_model_provider.py` stream 测试 8 passed |
| 前端 E2E 证据偏薄 | 补强 `agent-error-path.spec.ts`（模型依赖失败不泄露堆栈）与 `console-real-http.spec.ts`（统一 envelope） | 浏览器 E2E 覆盖与宣称一致 |
