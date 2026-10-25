# Tasks: 高 QPS 架构整改

- **Source**: `.code-flow/tasks/2026-10-24/high-qps-remediation/`（backend/frontend design + scenarios 合并）
- **Created**: 2026-10-25
- **Updated**: 2026-10-25
- **Plan-State**: TASK-001..007 done；TASK-008..012 draft

## Proposal

在保持开源默认 4 个部署单元（启用企微时 +1 个独立 Connector 镜像）和 PG+Redis 的前提下，先修掉 1000 并发对话的四个结构性瓶颈：SSE 长连接占 PG、每增量事务写放大、Console 固定 CPU/通知放大、企微连接与回复状态困在单 Gateway。任务按“先基线、再低风险热路径、再事件写/Connector、最后全量验收”排列，每个任务都是 1–2 天可演示能力。

### Alignment

- **Scope**: backend design FEAT-01..FEAT-10 + frontend FEAT-F01..F04；场景 31 条（2 E2E / 2 manual / 27 integration·component·contract）。
- **Decisions**: Connector 独立镜像但属于 im-gateway 运行时角色；WebChat SYNC/IM ASYNC；PG 持久事实 + Redis Stream 实时；默认不引入 Kafka/Citus/Service Mesh；身份缓存延后。
- **Non-goals**: 新 IM 协议实现、Mattermost ETL 执行、Citus/Kafka 默认引入、Console/Gateway HA 承诺。
- **Acceptance**: 1000×8 真实流式 SLO、SSE 连接不随流数线性增长、事件批量/保留、Connector lease/接管、前端 page_size/code splitting。

## Execution Contract

- 每个 TASK 是 1–2 天可独立演示能力；编码前先写局部行为测试并记录 RED，再实现和记录 GREEN。
- 单 worktree 一次只激活一个 TASK；无依赖且文件不相交的任务可按 `cf-task-start` 批次并行。
- E2E 只编写/登记，编码期不执行 RED/GREEN；统一由 verify-e2e 终验。
- 每个 required Rule 只有一个最终责任 TASK；其他任务通过依赖/契约引用，不重复认领。
- pytest/验收前先停 dev 服务，使用隔离验收库与独立 Redis 槽；不删共享 dev 数据。

## Task Index

| 任务 | 阶段 | 能力 | 优先级 | 预估 | 依赖 | 场景数 |
|---|---|---|---|---|---|---|
| TASK-001 | Phase 0 | 真实容量基线与分层观测 | P0 | 1.5d | - | 3 |
| TASK-002 | Phase 1 | Console 热路径去固定 CPU 与通知放大 | P0 | 2d | - | 3 |
| TASK-003 | Phase 1 | SSE 短 Session 与连接池预算 | P0 | 1.5d | - | 2 |
| TASK-004 | Phase 2 | 事件批量写、序列与保留 | P0 | 2d | TASK-003 | 3 |
| TASK-005 | Phase 2 | Runtime 容量、HPA 与后台隔离 | P0 | 2d | TASK-004 | 2 |
| TASK-006 | Phase 2 | 统一消息契约与网关内核 | P0 | 2d | - | 1 |
| TASK-007 | Phase 2 | Connector 运行时与企微状态隔离 | P0 | 2d | TASK-006 | 5 |
| TASK-008 | Phase 2 | Gateway 无状态、投递 facade 与 Webhook | P0 | 1.5d | TASK-006 | 1 |
| TASK-009 | Phase 2 | Gateway/Connector 切换、预检与多 worker 边界 | P0 | 2d | TASK-007, TASK-008 | 3 |
| TASK-010 | Phase 2 | WebChat 统一核心与读路径 | P0 | 1.5d | TASK-006, TASK-002 | 2 |
| TASK-011 | Phase 3 | 前端通知 page_size、轮询与 code splitting | P1 | 1.5d | TASK-002 | 3 |
| TASK-012 | Phase 3 | 全量验收、混沌与发布门禁 | P0 | 2d | TASK-001, TASK-003, TASK-004, TASK-005, TASK-009, TASK-010, TASK-011 | 3 |

---

## Acceptance Coverage

| 场景ID | 来源设计 | 测试层级 | 关键真实边界 | 负责任务 | 状态 | 执行命令 | cwd | timeout | 优先级 | 执行方式 | 环境 | 输入 | E2E理由 | 触发路径 | 冒烟 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| B-01 | high-qps-remediation.backend.design.md#2.5 验收条件 | integration | 真实 Gateway/Runtime/PG；ramp=0；本地 100 用户瞬时 burst，1000 用户 staging（TASK-012） | TASK-005 | e2e_deferred | ["uv", "run", "pytest", "-q", "tests/acceptance/capacity/test_capacity_admission_burst.py"] | . | 1200 | P0 | deferred | local-api | - | - | - | no |
| B-02 | high-qps-remediation.backend.design.md#2.5 验收条件 | integration | 真实 PostgreSQL；批量写入 + retention sweep | TASK-004 | verified | ["uv", "run", "pytest", "-q", "tests/agent_runtime/test_capacity_webchat_retention.py", "-k", "drains_20k"] | . | 600 | P0 | auto | local-api | - | - | - | no |
| B-03 | high-qps-remediation.backend.design.md#2.5 验收条件 | integration | 真实 uvicorn 2+ workers；/metrics 与 artifact fetch token | TASK-009 | planned | ["uv", "run", "pytest", "-q", "tests/architecture/test_multi_worker_state.py"] | . | 600 | P1 | auto | local-api | - | - | - | no |
| B-04 | high-qps-remediation.frontend.design.md#2.4 验收条件 | E2E | 真实浏览器 → Ingress → console-web/CDN + console-api + gateway；同源 Cookie | TASK-011 | planned | ["bash", "-lc", "npm --prefix apps/console-platform/frontend run build && npm --prefix e2e test -- --config playwright.webchat.config.ts static-overlay.spec.ts"] | . | 1800 | P1 | deferred | browser | - | 浏览器同源策略、Cookie、CSRF、Ingress 路径与 SSE 只有真实浏览器 + 真实代理才能验证。 | ["apps/console-platform/frontend/**", "deploy/k8s/base/ingress.yaml", "deploy/k8s/overlays/console-web/**"] | no |
| E-01 | high-qps-remediation.backend.design.md#2.5 验收条件 | integration | 真实 Gateway/Runtime + Console auth；模拟 platform-settings 不可用 | TASK-002 | verified | ["uv", "run", "pytest", "-q", "tests/acceptance/capacity/test_capacity_notification_settings.py"] | . | 900 | P1 | auto | local-api | - | - | - | no |
| E-02 | high-qps-remediation.backend.design.md#2.5 验收条件 | integration | 真实 Runtime + PostgreSQL；pool=1，打开 10 条 SSE | TASK-003 | verified | ["uv", "run", "pytest", "-q", "tests/acceptance/capacity/test_stream_delivery.py", "-k", "pool_timeout_is_bounded or early_close"] | . | 600 | P0 | auto | local-api | - | - | - | no |
| E-03 | high-qps-remediation.backend.design.md#2.5 验收条件 | integration | 真实 Connector 进程 + Redis Stream；kill -9 在 WS 收到后、XADD 前 | TASK-007 | e2e_deferred | ["uv", "run", "pytest", "-q", "tests/acceptance/im_gateway/test_connector_failover.py"] | . | 900 | P0 | deferred | local-api | - | - | - | no |
| E-04 | high-qps-remediation.backend.design.md#2.5 验收条件 | integration | 真实 Connector + 假 WS 服务端；旧 reply_ref 过期后发送终态/产物 | TASK-007 | verified | ["uv", "run", "pytest", "-q", "tests/acceptance/im_gateway/test_connector_failover.py"] | . | 900 | P1 | auto | local-api | - | - | - | no |
| E-05 | high-qps-remediation.backend.design.md#2.5 验收条件 | integration | 真实 Runtime + Redis 停机 + PG 持久事件 | TASK-004 | verified | ["uv", "run", "pytest", "-q", "tests/agent_runtime/test_capacity_webchat_shared_tail.py", "-k", "lost_hint_falls_back or hint_failure_keeps"] | . | 600 | P1 | auto | local-api | - | - | - | no |
| E-06 | high-qps-remediation.backend.design.md#2.5 验收条件 | manual | 真实企微智能机器人 + 两个 Connector 候选 + 真实网络；不是假 WS | TASK-007 | planned | - | . | 600 | P0 | manual | staging | - | - | - | no |
| E-07 | high-qps-remediation.backend.design.md#2.5 验收条件 | integration | 真实 Connector 进程 + Redis Stream；关闭平台重投 | TASK-007 | e2e_deferred | ["uv", "run", "pytest", "-q", "tests/acceptance/im_gateway/test_connector_failover.py"] | . | 900 | P0 | deferred | local-api | - | - | - | no |
| E-08 | high-qps-remediation.backend.design.md#2.5 验收条件 | integration | 真实进程 + PG/Redis 故障注入 | TASK-012 | planned | ["uv", "run", "pytest", "-q", "tests/acceptance/capacity/test_capacity_chaos.py"] | . | 900 | P0 | deferred | local-api | - | - | - | no |
| E-09 | high-qps-remediation.backend.design.md#2.5 验收条件 | manual | staging 目标拓扑；人工执行并记录证据 | TASK-012 | planned | - | . | 3600 | P0 | manual | staging | - | - | - | no |
| S-01 | high-qps-remediation.backend.design.md#2.5 验收条件 | integration | 真实入口代理 → Gateway → Runtime → PostgreSQL/Redis；模型为可控 HTTP 流式探针；通知轮询为浏览器同形请求 | TASK-012 | planned | ["bash", "-lc", "uv run pytest -q -m capacity_load tests/acceptance/capacity/test_capacity.py"] | . | 5400 | P0 | deferred | local-api | - | - | - | no |
| S-02 | high-qps-remediation.backend.design.md#2.5 验收条件 | integration | 浏览器同形通知轮询 → Gateway → Console auth / Runtime notifications；真实 PG/Redis | TASK-002 | e2e_deferred | ["bash", "-lc", "CAPACITY_NOTIFICATION_USERS=1000 CAPACITY_LOAD_USERS=1000 uv run pytest -q tests/acceptance/capacity/test_capacity_notifications.py -k s02"] | . | 900 | P0 | deferred | local-api | - | - | - | no |
| S-03 | high-qps-remediation.backend.design.md#2.5 验收条件 | integration | 真实 Gateway/Runtime HTTP SSE + 真实 PostgreSQL；runtime DB pool=2 | TASK-003 | verified | ["uv", "run", "pytest", "-q", "tests/acceptance/capacity/test_stream_delivery.py", "-k", "pool_two_hundred"] | . | 600 | P0 | auto | local-api | - | - | - | no |
| S-04 | high-qps-remediation.backend.design.md#2.5 验收条件 | integration | 真实 Runtime 事件 writer + 真实 PostgreSQL/Redis；20 delta/s × 10s | TASK-004 | verified | ["uv", "run", "pytest", "-q", "tests/agent_runtime/test_capacity_webchat_durable_deltas.py", "-k", "production_window_bounds_commits"] | . | 600 | P0 | auto | local-api | - | - | - | no |
| S-05 | high-qps-remediation.backend.design.md#2.5 验收条件 | integration | 两个真实 Connector 进程 + 假企微 WS 服务端 + 真实 Redis lease；Runtime/Gateway 真实进程 | TASK-007 | e2e_deferred | ["uv", "run", "pytest", "-q", "tests/acceptance/im_gateway/test_connector_lease.py"] | . | 900 | P0 | deferred | local-api | - | - | - | no |
| S-06 | high-qps-remediation.backend.design.md#2.5 验收条件 | E2E | 真实浏览器/HTTP 客户端 → 两个以上 Gateway 副本 → Runtime/PG/Redis；SSE 断线重连到另一副本 | TASK-009 | planned | ["bash", "-lc", "npm --prefix apps/console-platform/frontend run build && npm --prefix e2e test -- --config playwright.webchat.config.ts capacity-recovery.spec.ts"] | . | 1800 | P0 | deferred | browser | - | SSE 长连接、负载均衡、断线重连和跨 Pod 游标只有真实浏览器 + 多副本 + Runtime 持久流才能覆盖。 | ["apps/im-gateway/**", "apps/agent-runtime/src/muad_agent_runtime/api/runs.py", "apps/agent-runtime/src/muad_agent_runtime/application/event_tail_hub.py", "deploy/k8s/base/im-gateway.yaml"] | yes |
| S-07 | high-qps-remediation.backend.design.md#2.5 验收条件 | integration | 真实 Gateway HTTP /channels/{channel}/events + 假 webhook 提供方 + Redis Stream + Runtime | TASK-008 | verified | ["uv", "run", "pytest", "-q", "tests/acceptance/im_gateway/test_webhook_channel.py"] | . | 600 | P1 | auto | local-api | - | - | - | no |
| S-08 | high-qps-remediation.backend.design.md#2.5 验收条件 | integration | 真实 Gateway → 统一消息核心 → Console/Runtime/PG；已验证 CHAT 主体上下文 | TASK-010 | planned | ["uv", "run", "pytest", "-q", "tests/acceptance/im_gateway/test_webchat_core.py"] | . | 600 | P0 | auto | local-api | - | - | - | no |
| S-09 | high-qps-remediation.frontend.design.md#2.4 验收条件 | component | 前端 service/hook + mock HTTP；不访问真实后端 | TASK-011 | planned | ["uv", "run", "pytest", "-q", "tests/frontend/test_capacity_webchat_activity_ui.py"] | . | 600 | P1 | auto | none | - | - | - | no |
| S-10 | high-qps-remediation.frontend.design.md#2.4 验收条件 | component | 构建产物 + 前端路由（mock API） | TASK-011 | planned | ["uv", "run", "pytest", "-q", "tests/frontend/test_webchat_code_splitting.py"] | . | 600 | P1 | auto | none | - | - | - | no |
| S-11 | high-qps-remediation.backend.design.md#2.5 验收条件 | contract | 纯契约/序列化；不连服务 | TASK-006 | verified | ["uv", "run", "pytest", "-q", "tests/contracts/test_im_contracts.py"] | . | 600 | P0 | auto | none | - | - | - | no |
| S-12 | high-qps-remediation.backend.design.md#2.5 验收条件 | integration | 真实包导入/清单渲染/architecture tests；不启动业务栈 | TASK-009 | planned | ["uv", "run", "pytest", "-q", "tests/architecture"] | . | 600 | P0 | auto | none | - | - | - | no |
| S-13 | high-qps-remediation.backend.design.md#2.5 验收条件 | integration | 真实 Runtime/Worker/PG + 模拟队列压力 | TASK-005 | verified | ["uv", "run", "pytest", "-q", "tests/agent_runtime/test_runtime_capacity_metrics.py"] | . | 900 | P0 | auto | local-api | - | - | - | no |
| S-14 | high-qps-remediation.backend.design.md#2.5 验收条件 | integration | 真实 Console/Runtime/PG；记录 SQL/设置读取 | TASK-002 | verified | ["bash", "-lc", "uv run pytest -q tests/console_internal/test_admission_materials_api.py tests/console_auth/test_capacity_webchat_chat_session_service.py tests/agent_runtime/test_capacity_webchat_activity_read.py tests/console_platform/test_platform_settings_singleton.py"] | . | 900 | P0 | auto | local-api | - | - | - | no |
| S-15 | high-qps-remediation.backend.design.md#2.5 验收条件 | integration | 真实 Gateway/Runtime/PG；SQL 计数 | TASK-010 | planned | ["uv", "run", "pytest", "-q", "tests/agent_runtime/test_runs_api.py"] | . | 600 | P0 | auto | local-api | - | - | - | no |
| S-16 | high-qps-remediation.backend.design.md#2.5 验收条件 | integration | 真实栈 + 100 用户 × 1 轮 × 20 chunks/s + 通知轮询 | TASK-001 | e2e_deferred | ["bash", "-lc", "CAPACITY_LOAD_USERS=100 uv run pytest -q tests/acceptance/capacity/test_capacity_smoke.py"] | . | 1800 | P0 | deferred | local-api | - | - | - | no |
| S-17 | high-qps-remediation.backend.design.md#2.5 验收条件 | integration | 真实栈 + 200 用户 × 1 轮 | TASK-001 | e2e_deferred | ["bash", "-lc", "CAPACITY_LOAD_USERS=200 uv run pytest -q tests/acceptance/capacity/test_capacity.py -k s17_200_user_bounded_verification"] | . | 1800 | P1 | deferred | local-api | - | - | - | no |
| S-18 | high-qps-remediation.backend.design.md#2.5 验收条件 | integration | 真实栈 + 400 用户 + 通知轮询，30–60 分钟 | TASK-001 | e2e_deferred | ["bash", "-lc", "MUAD_CAPACITY_SOAK=1 uv run pytest -q tests/acceptance/capacity/test_capacity_soak.py"] | . | 3600 | P1 | deferred | local-api | - | - | - | no |

> 本表覆盖全部 31 个场景；测试层级/执行方式/E2E 理由/触发路径以 scenarios 源为准，本表仅 plan 投影。manual 场景无本地命令，执行方式为 manual。

---

## TASK-001: 真实容量基线与分层观测

- **Status**: done
- **Priority**: P0
- **Depends**: -
- **Source**: high-qps-remediation.backend.design.md#2.5 验收条件, high-qps-remediation.backend.design.md#3.5 质量实现方案
- **Spec-Refs**: -
- **Acceptance-Refs**: S-16, S-17, S-18
- **Estimate**: 1.5d
- **Capability**: 能跑 100/200/400 用户的分层容量基线，并输出可复现的 admission/first-delta/PG/CPU 报告

### Description

先把“假绿”变成可复现的失败基线：真实流式形状、浏览器同形通知轮询、SLO 断言、PG state/rate/retention 指标、CI 冒烟。

### Checklist

- [x] 交付 `<Capability>`，并提供 `make capacity-smoke` / `make capacity-load` / soak 的可重复命令
- [x] 扩展 capacity driver：1000 users × 8 turns × 600s、20 chunks/s × 10s、15s 通知轮询、steady 窗口、attempts=8000
- [x] 扩展 PG 采样：state 分解、idle-in-tx、commits/s、WAL/s、retention 速率、可选 pg_stat_statements
- [x] 在 acceptance 自身断言 SLO，不再只依赖 6 用户 report harness
- [x] [S-16][integration] 100 用户 smoke：真实栈 + 20 chunks/s，断言 admission/first-delta/错误/PG（测试已写；100 档基线已实测失败，终验延后）
- [x] [S-17][integration] 200 用户 bounded verification：断言 SLO 与“不得冒充 1000 结论”（nightly/deferred）
- [x] [S-18][integration] 400 用户 soak：断言无连接/内存泄漏、retention 追写入（30–60 分钟 gate，deferred）
- [x] 运行验收命令并填写 Acceptance Evidence

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| S-16 | integration | 真实 Gateway/Runtime/PG/Redis + 100 用户 × 1 轮 × 20 chunks/s | admission P95≤1000ms；first-delta P95≤2500ms；无 5xx/SSE 打开失败；PG total≤120 | tests/acceptance/capacity/test_capacity_smoke.py | ["bash", "-lc", "CAPACITY_LOAD_USERS=100 uv run pytest -q tests/acceptance/capacity/test_capacity_smoke.py"] | e2e_deferred |
| S-17 | integration | 真实栈 + 200 用户 × 1 轮 | 按 profile SLO 判定；不得冒充 1000 结论；无 pool timeout | tests/acceptance/capacity/test_capacity.py::test_s17_200_user_bounded_verification | ["bash", "-lc", "CAPACITY_LOAD_USERS=200 uv run pytest -q tests/acceptance/capacity/test_capacity.py -k s17_200_user_bounded_verification"] | e2e_deferred |
| S-18 | integration | 真实栈 + 400 用户 30–60 分钟 + 通知轮询 | 无连接/内存泄漏；PG/Redis 稳定；retention 追写入 | tests/acceptance/capacity/test_capacity_soak.py | ["bash", "-lc", "MUAD_CAPACITY_SOAK=1 uv run pytest -q tests/acceptance/capacity/test_capacity_soak.py"] | e2e_deferred |

### Acceptance Evidence

> 编码期由 `cf-task-start` 填写 RED/GREEN、断言位置与真实边界证据；manual 场景登记原因/边界/验收方式。

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|---|---|---|---|---|---|
| S-16 | 100 用户基线：99/100 受理；53 RUN_FAILED（COMMON_INTERNAL_ERROR）、8 SSE_REJECTED、5 TERMINAL_MISSING、4 ADMISSION_REJECTED；admission P95 7842ms；first-delta P95 13788ms；idle-in-tx 峰值 57；系统错误率 66% | 10 用户 harness 自检通过：10/10 受理、SLO 全过、通知轮询全 200；100 用户最终验收延后到 TASK-012 | `tests/acceptance/capacity/test_capacity_smoke.py` 的 SLO/PG/通知断言；`tests/capacity/slo.py` 逐项判定 | 四单元真实栈 + 入口代理 + 真实 PG/Redis + 200 帧模型探针 | not_included（deferred；基线已实测） |
| S-17 | 未执行（nightly/200 档；避免把 200 结论冒充 1000） | 测试已写：`test_s17_200_user_bounded_verification` 按 profile SLO 判定且显式 VERIFICATION | `tests/acceptance/capacity/test_capacity.py::test_s17_200_user_bounded_verification` | 同一真实四单元栈 + 200 个独立 CHAT 主体 | not_included（deferred） |
| S-18 | 未执行（30–60 分钟 soak 需显式门禁 `MUAD_CAPACITY_SOAK=1`） | 测试已写：真实 retention 行删除、RSS/FD、PG 连接稳态断言 | `tests/acceptance/capacity/test_capacity_soak.py` | 四单元真实栈 + 400 用户 + 通知轮询 + 过期事件行 | not_included（deferred） |
- S-16: e2e_deferred — automated command e2e_deferred; run_id=c5622a9188e649bdbfacb2cb5b76f9c3 (confirmed_by: runner)
- S-17: e2e_deferred — automated command e2e_deferred; run_id=c5622a9188e649bdbfacb2cb5b76f9c3 (confirmed_by: runner)
- S-18: e2e_deferred — automated command e2e_deferred; run_id=c5622a9188e649bdbfacb2cb5b76f9c3 (confirmed_by: runner)
- S-16: e2e_deferred — automated command e2e_deferred; run_id=388427d391ad4769a569289387582f11 (confirmed_by: runner)
- S-17: e2e_deferred — automated command e2e_deferred; run_id=388427d391ad4769a569289387582f11 (confirmed_by: runner)
- S-18: e2e_deferred — automated command e2e_deferred; run_id=388427d391ad4769a569289387582f11 (confirmed_by: runner)
- S-16: e2e_deferred — automated command e2e_deferred; run_id=40a3fc0fc6dc4f5f84b02ca54fedd027 (confirmed_by: runner)
- S-17: e2e_deferred — automated command e2e_deferred; run_id=40a3fc0fc6dc4f5f84b02ca54fedd027 (confirmed_by: runner)
- S-18: e2e_deferred — automated command e2e_deferred; run_id=40a3fc0fc6dc4f5f84b02ca54fedd027 (confirmed_by: runner)

### Baseline Finding (2026-10-25)

100 用户 smoke（100 users × 1 turn × 200 delta @50ms；生产节拍 30s/5s；runtime DB pool 30+30）在**当前代码**上实测失败，作为整改前基线保留：

- 受理 99/100；失败账目 `RUN_FAILED=53`（`COMMON_INTERNAL_ERROR`）、`SSE_REJECTED=8`、`TERMINAL_MISSING=5`、`ADMISSION_REJECTED=4`。
- admission P95 **7842ms**（目标 ≤1000ms）；first-delta P95 **13788ms**（目标 ≤2500ms）；idle-in-tx 峰值 **57**（目标 ≤20）；系统错误率 **66%**。
- 结论：这是 TASK-002（Console 热路径）、TASK-003（SSE 短 Session）、TASK-005（Runtime 容量）要消除的结构性瓶颈，不是 harness 误报；TASK-012 必须在整改后重跑 S-16/S-01/E-09。
- 10 用户 harness 自检通过（10/10 受理、SLO 全过、通知轮询全 200），证明采集/判定链路可用。

### Log

- [2026-10-25] created (draft)
- [2026-10-25] started
- [2026-10-25] implemented short SSE session (`release_session` before StreamingResponse), `get_session` in_transaction commit, stream-scope gate sized below pool; S-03 100 streams pass, E-02 pool timeout bounded; 7 stream tests passed
- [2026-10-25] started
- [2026-10-25] implemented process settings singleton, chat-principal JOIN (4→1 SELECT), notification idle fast path (4→2 SELECT), admission materials dedupe; tests: console_platform+console_auth 476 passed, console_internal 60 passed, notifications 10-user self-check 2 passed
- [2026-10-25] started
- [2026-10-25] implemented capacity baseline: driver notification/steady/attempts, PG state/rate/retention, SLO evaluator, S-16/S-17/S-18 tests, Makefile targets
- [2026-10-25] baseline measured: 10-user sanity pass; 100-user S-16 fails with real bottlenecks (recorded above)
- [2026-10-25] completed (done)

---

## TASK-002: Console 热路径去固定 CPU 与通知放大

- **Status**: done
- **Priority**: P0
- **Depends**: -
- **Source**: high-qps-remediation.backend.design.md#2.5 验收条件, high-qps-remediation.backend.design.md#3.5 质量实现方案
- **Spec-Refs**: harness-auth#RULE-auth-001
- **Acceptance-Refs**: S-02, S-14, E-01
- **Estimate**: 2d
- **Capability**: 1000 用户空闲轮询时 Console CPU 不再贴顶；认证/通知/受理材料不再逐请求构造 settings

### Description

settings 单例/注入、chat auth 5 SQL→2 SQL、通知 page_size/空闲 fast path、admission-materials 去重；不改授权语义。

### Checklist

- [x] 交付 `<Capability>`：settings 构造计数=0，chat-principal ≤3 SQL
- [x] 实现进程级 settings 注入；替换 `require_internal_service`/`_batch_platform_limit`/ConsoleSessionVerifier 的逐请求构造
- [x] 合并 chat auth 查询（session+token+user JOIN + grants），保留错误优先级
- [x] 通知请求带 page_size；空闲 after_seq==through_seq fast path；materials 去掉重复 model/credential 解析
- [x] [S-02][integration] 1000 用户 15s 轮询：无 Console settings 回源，CPU<60%，无 pool timeout（10 用户自检通过；1000 档 deferred）
- [x] [S-14][integration] materials/通知 fast path：记录 SQL/设置读取次数并断言下降
- [x] [E-01][integration] settings 源故障时已带 page_size 的轮询仍可用；auth 失败保留 401/403/409
- [x] [harness-auth#RULE-auth-001][verifier] bash -lc "uv run pytest -q tests/console_platform/test_user_side_relations.py -k s04 && uv run pytest -q tests -k schema_parity"
- [x] 运行验收命令并填写 Acceptance Evidence

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| S-02 | integration | 真实 Gateway/Console/Runtime/PG + 1000 个 15s 轮询 | settings 构造=0；chat-principal≤3 SQL；CPU<60%；无 pool timeout | tests/acceptance/capacity/test_capacity_notifications.py | ["bash", "-lc", "CAPACITY_NOTIFICATION_USERS=1000 CAPACITY_LOAD_USERS=1000 uv run pytest -q tests/acceptance/capacity/test_capacity_notifications.py -k s02"] | e2e_deferred |
| S-14 | integration | 真实 Console/Runtime/PG；记录 SQL/设置读取 | materials 不重复解析 model/credential；空闲轮询不做 count/page/unread 全跑；settings 只构造一次 | tests/console_internal/test_admission_materials_api.py::test_materials_resolve_model_and_credential_once; tests/console_auth/test_capacity_webchat_chat_session_service.py::test_resolve_uses_one_joined_query_and_preserves_error_priority; tests/agent_runtime/test_capacity_webchat_activity_read.py::test_b264_idle_cursor_skips_count_and_page_queries; tests/console_platform/test_platform_settings_singleton.py | ["bash", "-lc", "uv run pytest -q tests/console_internal/test_admission_materials_api.py tests/console_auth/test_capacity_webchat_chat_session_service.py tests/agent_runtime/test_capacity_webchat_activity_read.py tests/console_platform/test_platform_settings_singleton.py"] | verified |
| E-01 | integration | 真实 Gateway/Runtime + Console settings 故障 | 已带 page_size 的轮询不依赖 settings；auth 失败语义不变 | tests/acceptance/capacity/test_capacity_notification_settings.py | ["uv", "run", "pytest", "-q", "tests/acceptance/capacity/test_capacity_notification_settings.py"] | verified |

**Spec Verifier**

| Rule | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 执行命令 | 状态 |
|---|---|---|---|---|---|
| harness-auth#RULE-auth-001 | integration | 真实 PG/Redis/HTTP/architecture tests；不 mock 规则边界 | 规则既有 verifier 全绿；本任务不改变其语义 | bash -lc "uv run pytest -q tests/console_platform/test_user_side_relations.py -k s04 && uv run pytest -q tests -k schema_parity" | planned |

### Acceptance Evidence

> 编码期由 `cf-task-start` 填写 RED/GREEN、断言位置与真实边界证据；manual 场景登记原因/边界/验收方式。

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|---|---|---|---|---|---|
| S-02 | 空闲轮询原为 count/page/unread 三条查询；settings 逐请求构造 | 10 用户 × 15s 轮询全部 200；设置回源增量 ≤ 同长空闲基线；CPU ≤ 60% limit；1000 档 deferred | `tests/acceptance/capacity/test_capacity_notifications.py::test_s02...`；`test_platform_settings_singleton.py` | 真实 Gateway → Console chat-principal → Runtime/PG；真实 CHAT Cookie | not_included（deferred；10 用户自检通过） |
| S-14 | materials 请求 `model_definition` 2 条；`resolve` 4 条 SELECT；空闲通知 4 条 SELECT | materials `model_definition`/credential 各 1 条；`resolve` 1 条 JOIN；空闲通知 2 条 SELECT | `test_materials_resolve_model_and_credential_once`、`test_resolve_uses_one_joined_query...`、`test_b264_idle_cursor...`、`test_process_settings_is_constructed_once` | 真实 PG 行 + SQLAlchemy `before_cursor_execute` 计数 | verified |
| E-01 | 未单独构造 RED（设置读取路径由 S-14 覆盖） | 写入非法 settings_json revision 2 后，带 page_size 的通知轮询仍 200、page_size=20 | `tests/acceptance/capacity/test_capacity_notification_settings.py` | 真实 Console/Runtime/Gateway + 真实 PG 非法设置行 | not_included（deferred；10 用户自检通过） |
- E-01: failed — automated command failed; run_id=15b7f92073014c84a38e31139d0e260e (confirmed_by: runner)
- S-02: e2e_deferred — automated command e2e_deferred; run_id=15b7f92073014c84a38e31139d0e260e (confirmed_by: runner)
- S-14: verified — automated command passed; run_id=15b7f92073014c84a38e31139d0e260e (confirmed_by: runner)
- E-01: failed — automated command failed; run_id=50b12563b1234c9f981f313d92db3b1a (confirmed_by: runner)
- S-02: e2e_deferred — automated command e2e_deferred; run_id=50b12563b1234c9f981f313d92db3b1a (confirmed_by: runner)
- E-01: verified — automated command passed; run_id=1b37fc5dea11438e8b200d4034d208e0 (confirmed_by: runner)
- S-02: e2e_deferred — automated command e2e_deferred; run_id=1b37fc5dea11438e8b200d4034d208e0 (confirmed_by: runner)
- S-14: verified — automated command passed; run_id=1b37fc5dea11438e8b200d4034d208e0 (confirmed_by: runner)

### Log

- [2026-10-25] created (draft)
- [2026-10-25] started
- [2026-10-25] implemented short SSE session (`release_session` before StreamingResponse), `get_session` in_transaction commit, stream-scope gate sized below pool; S-03 100 streams pass, E-02 pool timeout bounded; 7 stream tests passed
- [2026-10-25] started
- [2026-10-25] implemented process settings singleton, chat-principal JOIN (4→1 SELECT), notification idle fast path (4→2 SELECT), admission materials dedupe; tests: console_platform+console_auth 476 passed, console_internal 60 passed, notifications 10-user self-check 2 passed
- [2026-10-25] started
- [2026-10-25] completed (done)

---

## TASK-003: SSE 短 Session 与连接池预算

- **Status**: done
- **Priority**: P0
- **Depends**: -
- **Source**: high-qps-remediation.backend.design.md#3.2 架构设计, high-qps-remediation.backend.design.md#3.5 质量实现方案
- **Spec-Refs**: -
- **Acceptance-Refs**: S-03, E-02
- **Estimate**: 1.5d
- **Capability**: 1000 条 SSE 不再占 1000 条 PG 连接；小池下打开 100 条流不超时

### Description

流式路由返回 StreamingResponse 前关闭请求级 Session；显式池、gate≤pool、preflight 预算；PG idle-in-tx 兜底。

### Checklist

- [x] 交付 `<Capability>`：pool=2 时 100 条 SSE 全部打开，PG total 不随流数线性增长
- [x] 拆分 RunStreamService/短 Session；tail/scope 校验后 commit/close 再返回流
- [x] `get_session` 只在 in_transaction 时 commit；显式池/pre_ping/timeout 调整
- [x] 准入材料阶段与写事务 gate 分离；preflight 校验 pool+overflow ≥ gate
- [x] [S-03][integration] pool=2 × 100 流：全部成功、PG total≤20、稳态 idle-in-tx≈0
- [x] [E-02][integration] pool=1 超容量：1s 内显式失败，不挂 5s、不抛未处理 QueuePool
- [ ] 运行验收命令并填写 Acceptance Evidence

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| S-03 | integration | 真实 Runtime HTTP SSE + PostgreSQL；pool=2 | 100 条流成功；PG total≤20；idle-in-tx≈0 | tests/acceptance/capacity/test_stream_delivery.py::test_s03_pool_two_hundred_sse_streams_do_not_hold_pg_connections | ["uv", "run", "pytest", "-q", "tests/acceptance/capacity/test_stream_delivery.py", "-k", "pool_two_hundred"] | verified |
| E-02 | integration | 真实 Runtime + PostgreSQL；pool=1/overflow=0/timeout=1s | 1s 内显式失败；无未处理 QueuePool；指标可见 | tests/acceptance/capacity/test_stream_delivery.py::test_e02_pool_timeout_is_bounded_to_configured_second | ["uv", "run", "pytest", "-q", "tests/acceptance/capacity/test_stream_delivery.py", "-k", "pool_timeout_is_bounded or early_close"] | verified |

### Acceptance Evidence

> 编码期由 `cf-task-start` 填写 RED/GREEN、断言位置与真实边界证据；manual 场景登记原因/边界/验收方式。

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|---|---|---|---|---|---|
| S-03 | pool=2 首次实测：2×200、98×500（`QueuePool limit of size 2`），请求级 Session 未释放 | 100/100 流打开；PG total≤20；稳态 idle-in-tx≤2；测试 1 passed | `test_s03_pool_two_hundred_sse_streams_do_not_hold_pg_connections` | 两个真实 Runtime 进程 + 真实 PG + 100 条真实 HTTP SSE | verified |
| E-02 | 池耗尽回落到 5s/30s 默认并冒未处理 `QueuePool`；提前 close 后 `get_session` 仍 commit | pool_timeout 约束在 ~1s；提前 close 后依赖收尾不 commit；`db_pool_size`/`db_pool_checked_out` 指标可见 | `test_e02_pool_timeout_is_bounded_to_configured_second`、`test_e02_get_session_does_not_commit_after_early_close` | 真实 PostgreSQL + SQLAlchemy pool=1/overflow=0 | verified |
- E-02: verified — automated command passed; run_id=e3b48428389f4695acc6ffb5d9725346 (confirmed_by: runner)
- S-03: verified — automated command passed; run_id=e3b48428389f4695acc6ffb5d9725346 (confirmed_by: runner)

### Log

- [2026-10-25] created (draft)
- [2026-10-25] started
- [2026-10-25] implemented short SSE session (`release_session` before StreamingResponse), `get_session` in_transaction commit, stream-scope gate sized below pool; S-03 100 streams pass, E-02 pool timeout bounded; 7 stream tests passed
- [2026-10-25] started
- [2026-10-25] completed (done)

---

## TASK-004: 事件批量写、序列与保留

- **Status**: done
- **Priority**: P0
- **Depends**: TASK-003
- **Source**: high-qps-remediation.backend.design.md#3.3 数据设计, high-qps-remediation.backend.design.md#3.5 质量实现方案
- **Spec-Refs**: harness-data#RULE-data-001, harness-snapshot#RULE-snapshot-001
- **Acceptance-Refs**: S-04, E-05, B-02
- **Estimate**: 2d
- **Capability**: 1000 在飞 Run 的 commits/s 从 2 万降到 2–4k，事件不丢不重且 retention 追得上

### Description

per-Run 批量 writer、seq 块、lease fence、批量 INSERT；去掉每事件行锁；retention/索引/分区条件。

### Checklist

- [x] 交付 `<Capability>`：100 Run × 20 delta/s 下 ≤4 commits/s/Run、≤3 statements/event
- [x] 实现 per-Run RunEventWriter（首段立即 flush，后续 250–500ms/8–16KB）
- [x] 批次级 lease fence + seq 块 + 批量 INSERT；去掉每事件 lock_run_source/database_now
- [x] 事件带 execution_epoch；terminal 最后；SIGKILL 前缀可 replay
- [x] retention 批量/索引/分区；新索引/列遵守标准列/timestamptz
- [x] [S-04][integration] 批量写/重放/终态/SIGKILL 断言
- [x] [E-05][integration] Redis 故障退 PG 有界补查，恢复回切
- [x] [B-02][integration] 20k rows/s 等价写入下 retention 删除≥写入
- [x] [harness-data#RULE-data-001][verifier] uv run pytest -q tests -k schema_parity
- [x] [harness-snapshot#RULE-snapshot-001][verifier] bash -lc "uv run pytest -q tests/agent_runtime/test_snapshot_freeze.py tests/agent_runtime/test_run_reaper.py && uv run pytest -q tests/agent_runtime -k 'executor or resolve'"
- [x] 运行验收命令并填写 Acceptance Evidence

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| S-04 | integration | 真实 Runtime 事件 writer + PostgreSQL/Redis；20 delta/s × 10s | ≤4 commits/s/Run；≤3 statements/event；无丢/重 seq；terminal 最后 | tests/agent_runtime/test_capacity_webchat_durable_deltas.py::test_b259_production_window_bounds_commits_per_second | ["uv", "run", "pytest", "-q", "tests/agent_runtime/test_capacity_webchat_durable_deltas.py", "-k", "production_window_bounds_commits"] | verified |
| E-05 | integration | 真实 Runtime + Redis 停机 + PG 持久事件 | 退化为 PG 有界轮询；不丢/不重；恢复回切 | tests/agent_runtime/test_capacity_webchat_shared_tail.py::test_b260_lost_hint_falls_back_to_bounded_pg_catch_up; test_b260_hint_failure_keeps_writes_and_local_wakeup | ["uv", "run", "pytest", "-q", "tests/agent_runtime/test_capacity_webchat_shared_tail.py", "-k", "lost_hint_falls_back or hint_failure_keeps"] | verified |
| B-02 | integration | 真实 PostgreSQL；批量写入 + retention | 删除速率≥写入；表大小稳定；无长事务/锁等待 | tests/agent_runtime/test_capacity_webchat_retention.py::test_b269_stream_replay_cleanup_drains_20k_backlog | ["uv", "run", "pytest", "-q", "tests/agent_runtime/test_capacity_webchat_retention.py", "-k", "drains_20k"] | verified |

**Spec Verifier**

| Rule | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 执行命令 | 状态 |
|---|---|---|---|---|---|
| harness-data#RULE-data-001 | integration | 真实 PG/Redis/HTTP/architecture tests；不 mock 规则边界 | 规则既有 verifier 全绿；本任务不改变其语义 | uv run pytest -q tests -k schema_parity | planned |
| harness-snapshot#RULE-snapshot-001 | integration | 真实 PG/Redis/HTTP/architecture tests；不 mock 规则边界 | 规则既有 verifier 全绿；本任务不改变其语义 | bash -lc "uv run pytest -q tests/agent_runtime/test_snapshot_freeze.py tests/agent_runtime/test_run_reaper.py && uv run pytest -q tests/agent_runtime -k 'executor or resolve'" | planned |

### Acceptance Evidence

> 编码期由 `cf-task-start` 填写 RED/GREEN、断言位置与真实边界证据；manual 场景登记原因/边界/验收方式。

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|---|---|---|---|---|---|
| S-04 | 生产默认 50ms/4096：20 delta/s ≈ 20 commits/s/Run，逐窗口事务 | 生产默认 300ms/8192 + 首段立即 flush；20 delta/s 实测 ≤4.5 commits/s，200 delta 仅 43 条 INSERT；正文无损、终态最后 | `test_b259_production_window_bounds_commits_per_second`、`test_b259_production_window_flushes_first_delta_immediately` | 真实 Runtime executor + 真实 PostgreSQL canonical 事件 | verified |
| E-05 | 既有 hint 丢失/PG 补查用例 | Redis 提示失败不阻塞写入，PG 有界补查补齐；2 tests passed | `test_capacity_webchat_shared_tail.py` 的 lost_hint/hint_failure | 真实 Runtime + Redis/EventTailHub + PG | verified |
| B-02 | 既有 retention 功能用例覆盖保护/边界，未覆盖 20k 排空吞吐 | 20k 过期流增量真实排空；删除速率 ≥1k rows/s；无 waiting locks | `test_b269_stream_replay_cleanup_drains_20k_backlog` | 真实 PostgreSQL 批量 retention | verified |
- B-02: failed — automated command failed; run_id=701fc4ea81484d5b9d8179db0ad27429 (confirmed_by: runner)
- E-05: failed — automated command failed; run_id=701fc4ea81484d5b9d8179db0ad27429 (confirmed_by: runner)
- S-04: verified — automated command passed; run_id=701fc4ea81484d5b9d8179db0ad27429 (confirmed_by: runner)
- B-02: verified — automated command passed; run_id=46c8602419f6455dabd8f837f9289563 (confirmed_by: runner)
- E-05: verified — automated command passed; run_id=46c8602419f6455dabd8f837f9289563 (confirmed_by: runner)
- S-04: verified — automated command passed; run_id=46c8602419f6455dabd8f837f9289563 (confirmed_by: runner)

### Log

- [2026-10-25] created (draft)
- [2026-10-25] started
- [2026-10-25] production window 300ms/8192 + first-delta immediate flush; S-04 commit-rate test pass, E-05 hint fallback pass, B-02 20k retention backlog drain pass; schema_parity/snapshot verifiers pass
- [2026-10-25] started
- [2026-10-25] implemented process settings singleton, chat-principal JOIN (4→1 SELECT), notification idle fast path (4→2 SELECT), admission materials dedupe; tests: console_platform+console_auth 476 passed, console_internal 60 passed, notifications 10-user self-check 2 passed
- [2026-10-25] completed (done)

---

## TASK-005: Runtime 容量、HPA 与后台隔离

- **Status**: done
- **Priority**: P0
- **Depends**: TASK-004
- **Source**: high-qps-remediation.backend.design.md#3.5 质量实现方案, high-qps-remediation.backend.design.md#4.1 部署架构
- **Spec-Refs**: harness-memory#RULE-memory-001, harness-memory#RULE-memory-002, harness-memory#RULE-memory-003, harness-memory#RULE-memory-004, harness-memory#RULE-memory-005, harness-memory#RULE-memory-006, harness-worker#RULE-worker-001
- **Acceptance-Refs**: S-13, B-01
- **Estimate**: 2d
- **Capability**: 1000 Run 不因 32 槽位/CPU-only HPA/memory backlog 排队到超时；过载有界拒绝

### Description

队列 gauge/HPA、执行槽/模型额度、history 移出受理、memory consolidation 背压、后台独立池。

### Checklist

- [x] 交付 `<Capability>`：队列年龄可观测、HPA 按队列扩、history 不占 202、memory backlog 有界
- [x] 发布 waiting/oldest/slots/event flush/pool 指标；HPA 配置队列指标
- [x] 提高执行槽/副本与模型池额度，总槽位按 profile 达 1000
- [x] `_load_history` 移到后台 launch；memory consolidation 限速/独立池
- [x] [S-13][integration] 队列指标/HPA、202 不等 history、memory backlog 有界
- [x] [B-01][integration] 本地 100 用户瞬时 burst：有界队列/429、不 OOM、恢复后重新受理；1000 用户由 TASK-012 staging 执行
- [x] [harness-memory#RULE-memory-001][verifier] uv run pytest -q tests/agent_core/test_memory_redesign_evidence.py tests/agent_runtime/test_memory_redesign_confirmation.py
- [x] [harness-memory#RULE-memory-002][verifier] uv run pytest -q tests/agent_runtime/test_memory_redesign_writes.py tests/agent_runtime/test_memory_redesign_quotas.py
- [x] [harness-memory#RULE-memory-003][verifier] uv run pytest -q tests/agent_runtime/test_memory_redesign_cache.py tests/agent_runtime/test_memory_redesign_context.py tests/agent_core/test_memory_redesign_selection.py
- [x] [harness-memory#RULE-memory-004][verifier] uv run pytest -q tests/agent_runtime/test_memory_redesign_job_effects.py tests/agent_runtime/test_memory_redesign_confirmation.py
- [x] [harness-memory#RULE-memory-005][verifier] uv run pytest -q tests/agent_runtime/test_memory_redesign_job_effects.py tests/agent_runtime/test_memory_redesign_input_boundary.py
- [x] [harness-memory#RULE-memory-006][verifier] uv run pytest -q tests/migrations/test_memory_redesign_migration.py tests/agent_runtime/test_memory_redesign_snapshot.py
- [x] [harness-worker#RULE-worker-001][verifier] bash -lc "uv run pytest -q tests/agent_worker && uv run pytest -q tests/agent_runtime --ignore=tests/agent_runtime/test_runner_executor.py"
- [x] 运行验收命令并填写 Acceptance Evidence

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| S-13 | integration | 真实 Runtime/Worker/PG + 模拟队列压力 | 队列年龄可断言；202 不等待 history；memory backlog 有界 | tests/agent_runtime/test_runtime_capacity_metrics.py | ["uv", "run", "pytest", "-q", "tests/agent_runtime/test_runtime_capacity_metrics.py"] | verified |
| B-01 | integration | 真实 Gateway/Runtime/PG；ramp=0；本地 100 用户 burst | 不 OOM/无限 task；显式 429；已受理可查；恢复排空；1000 用户 staging | tests/acceptance/capacity/test_capacity_admission_burst.py | ["uv", "run", "pytest", "-q", "tests/acceptance/capacity/test_capacity_admission_burst.py"] | deferred | e2e_deferred |

**Spec Verifier**

| Rule | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 执行命令 | 状态 |
|---|---|---|---|---|---|
| harness-memory#RULE-memory-001 | integration | 真实 PG/Redis/HTTP/architecture tests；不 mock 规则边界 | 规则既有 verifier 全绿；本任务不改变其语义 | uv run pytest -q tests/agent_core/test_memory_redesign_evidence.py tests/agent_runtime/test_memory_redesign_confirmation.py | planned |
| harness-memory#RULE-memory-002 | integration | 真实 PG/Redis/HTTP/architecture tests；不 mock 规则边界 | 规则既有 verifier 全绿；本任务不改变其语义 | uv run pytest -q tests/agent_runtime/test_memory_redesign_writes.py tests/agent_runtime/test_memory_redesign_quotas.py | planned |
| harness-memory#RULE-memory-003 | integration | 真实 PG/Redis/HTTP/architecture tests；不 mock 规则边界 | 规则既有 verifier 全绿；本任务不改变其语义 | uv run pytest -q tests/agent_runtime/test_memory_redesign_cache.py tests/agent_runtime/test_memory_redesign_context.py tests/agent_core/test_memory_redesign_selection.py | planned |
| harness-memory#RULE-memory-004 | integration | 真实 PG/Redis/HTTP/architecture tests；不 mock 规则边界 | 规则既有 verifier 全绿；本任务不改变其语义 | uv run pytest -q tests/agent_runtime/test_memory_redesign_job_effects.py tests/agent_runtime/test_memory_redesign_confirmation.py | planned |
| harness-memory#RULE-memory-005 | integration | 真实 PG/Redis/HTTP/architecture tests；不 mock 规则边界 | 规则既有 verifier 全绿；本任务不改变其语义 | uv run pytest -q tests/agent_runtime/test_memory_redesign_job_effects.py tests/agent_runtime/test_memory_redesign_input_boundary.py | planned |
| harness-memory#RULE-memory-006 | integration | 真实 PG/Redis/HTTP/architecture tests；不 mock 规则边界 | 规则既有 verifier 全绿；本任务不改变其语义 | uv run pytest -q tests/migrations/test_memory_redesign_migration.py tests/agent_runtime/test_memory_redesign_snapshot.py | planned |
| harness-worker#RULE-worker-001 | integration | 真实 PG/Redis/HTTP/architecture tests；不 mock 规则边界 | 规则既有 verifier 全绿；本任务不改变其语义 | bash -lc "uv run pytest -q tests/agent_worker && uv run pytest -q tests/agent_runtime --ignore=tests/agent_runtime/test_runner_executor.py" | planned |

### Acceptance Evidence

> 编码期由 `cf-task-start` 填写 RED/GREEN、断言位置与真实边界证据；manual 场景登记原因/边界/验收方式。

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|---|---|---|---|---|---|
| S-13 | TASK-005 早期 100 用户 smoke 只有失败基线（admission P95 4764ms），队列 gauge/“202 不等 history”/backlog 无断言 | `uv run pytest -q tests/agent_runtime/test_runtime_capacity_metrics.py` → 4 passed；`test_capacity_webchat_admission_create.py -k s13` → 1 passed | `test_runtime_capacity_metrics.py::test_s13_capacity_gauges_read_real_queue_and_memory_rows`、`test_s13_admission_gate_*`；`test_capacity_webchat_admission_create.py::test_s13_admission_returns_before_history_loader_finishes` | 隔离 PG（alembic head）真实 `runtime.run_record`/`memory_consolidation_job`，队列年龄由 PG `now()` 计算；指标经 `/metrics` 文本导出；202 在 history 释放前已持久 | verified |
| B-01 | 突发曾出现 Gateway egress `PoolTimeout` 与 `CONVERSATION_REJECTED`；队列无有界拒绝断言 | `uv run pytest -q tests/acceptance/capacity/test_capacity_admission_burst.py` → 1 passed（100 用户 ramp=0，约 35s） | 只允许显式 429；已受理 Run 全部可在 PG 查到；90s 内 `open_runs=0`；过载窗口后重新建会话/提交成功；Runtime RSS < 8192MB | 四单元真实进程 + 入口代理 + 真实 PG/Redis + 真实 HTTP 模型探针；本地 100 用户证据，1000 用户由 TASK-012/S-01/E-09 在 staging 执行 | deferred |

> 历史阻塞已解除：100 用户 smoke 的 `RUN_FAILED/system error` 已归零；本机单进程栈的 client-side SLO 仍高于 TASK-001 阈值（最终记录 admission P95 1848ms / first-delta P95 6870ms / idle-in-tx 34），不作为 S-16 通过结论，1000 用户 release gate 由 TASK-012 的 S-01/E-09 在 staging 执行。
- B-01: e2e_deferred — automated command e2e_deferred; run_id=00d383bae4e34f43a714b99c0c736517 (confirmed_by: runner)
- S-13: verified — automated command passed; run_id=00d383bae4e34f43a714b99c0c736517 (confirmed_by: runner)
- B-01: e2e_deferred — automated command e2e_deferred; run_id=99dcc6d9a0554d0880d13cd4ab27f259 (confirmed_by: runner)
- S-13: verified — automated command passed; run_id=99dcc6d9a0554d0880d13cd4ab27f259 (confirmed_by: runner)
- B-01: e2e_deferred — automated command e2e_deferred; run_id=a1d5e14565a542d9a5182223bb95cb45 (confirmed_by: runner)
- S-13: verified — automated command passed; run_id=a1d5e14565a542d9a5182223bb95cb45 (confirmed_by: runner)

### Log

- [2026-10-25] created (draft)
- [2026-10-25] started
- [2026-10-25] implemented short SSE session (`release_session` before StreamingResponse), `get_session` in_transaction commit, stream-scope gate sized below pool; S-03 100 streams pass, E-02 pool timeout bounded; 7 stream tests passed
- [2026-10-25] started
- [2026-10-25] implemented process settings singleton, chat-principal JOIN (4→1 SELECT), notification idle fast path (4→2 SELECT), admission materials dedupe; tests: console_platform+console_auth 476 passed, console_internal 60 passed, notifications 10-user self-check 2 passed
- [2026-10-25] blocked (Runtime capacity/HPA/queue metrics/memory backpressure 仍需专项实现；history 已移到后台 launch（202 不再等历史），100 用户 smoke 仍失败：admission P95 4764ms、first-delta P95 9864ms、system error 40%、idle-in-tx 51。剩余项交给后续 TASK-005 续作。)
- [2026-10-25] resumed (draft)
- [2026-10-25] blocked (100 用户 smoke 仍失败：admission P95 3767ms、first-delta P95 9877ms、system error 20%、idle-in-tx 39。已定位本栈 PG max_connections 被打满（30+30×2 Runtime + Console/Gateway 池 > 实例上限），缩到 10+10 后 system error 66%→20%；剩余需 HPA 队列指标、执行槽/模型额度定容、Console 单进程容量与 memory 背压专项，不能在本轮安全收口。)
- [2026-10-25] implemented queue/oldest/inflight/open/memory/admission gauges + sampling loop, queue-based HPA, 256 execution slots × 4 replicas, bounded admission gate with 429, memory enqueue cap, history deferred, short read sessions for get_run/tail/notifications, response-header admission, 300ms/8192 delta flush; S-13 4 passed; B-01 100-user burst passed
- [2026-10-25] final 100-user smoke: RUN_FAILED=0, admission P95 1848ms, first-delta P95 6870ms, idle-in-tx 34; still above S-16 client-side thresholds on local single-process stack, deferred to TASK-012
- [2026-10-25] completed (done)

---

## TASK-006: 统一消息契约与网关内核

- **Status**: done
- **Priority**: P0
- **Depends**: -
- **Source**: high-qps-remediation.backend.design.md#3.4 接口设计, high-qps-remediation.backend.design.md#3.2 架构设计
- **Spec-Refs**: -
- **Acceptance-Refs**: S-11
- **Estimate**: 2d
- **Capability**: 新增 IM/Webhook 只写 adapter：InboundEvent/DeliveryCommand/Capabilities/Lease 可独立契约测试

### Description

定义统一入站/出站契约、route/shard key、能力清单、lease/fencing；提供 MessageGateway SYNC/ASYNC 内核。

### Checklist

- [x] 交付 `<Capability>`：四类契约 JSON round-trip 与未知字段拒绝
- [x] 新增 `muad_contracts/im.py` 与 `packages/im-core` SPI/route/stream 助手
- [x] 打开 ChannelName/registry；保留旧 WebChat/IM 契约兼容（新契约 channel 用开放 string；旧 ChannelName/Envelope 保留）
- [x] 实现 `admit_sync`/`admit_async` 内核入口与能力路由
- [x] [S-11][contract] 四类契约 round-trip、shard/route key 稳定、旧契约不破坏
- [x] 运行验收命令并填写 Acceptance Evidence

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| S-11 | contract | 纯契约/序列化；不连服务 | 字段/枚举/未知字段拒绝符合契约；同输入 shard 稳定；新通道可扩展；旧契约不破坏 | tests/contracts/test_im_contracts.py | ["uv", "run", "pytest", "-q", "tests/contracts/test_im_contracts.py"] | verified |

### Acceptance Evidence

> 编码期由 `cf-task-start` 填写 RED/GREEN、断言位置与真实边界证据；manual 场景登记原因/边界/验收方式。

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|---|---|---|---|---|---|
| S-11 | 尚无统一契约；`ChannelEnvelope` 仅 WECOM/WEBCHAT | 四类契约 round-trip、未知字段拒绝、strict payload、稳定 route/shard/key、SLACK 新通道、SYNC/ASYNC 内核与旧契约兼容；7 passed | `tests/contracts/test_im_contracts.py` | 纯契约/序列化；不连服务 | verified |
- S-11: verified — automated command passed; run_id=f86381c920464b23aa5703b9d4ba91e0 (confirmed_by: runner)

### Log

- [2026-10-25] created (draft)
- [2026-10-25] implemented unified IM contracts and im-core SPI/gateway/streams; 7 contract tests passed; mypy/ruff clean
- [2026-10-25] started
- [2026-10-25] implemented short SSE session (`release_session` before StreamingResponse), `get_session` in_transaction commit, stream-scope gate sized below pool; S-03 100 streams pass, E-02 pool timeout bounded; 7 stream tests passed
- [2026-10-25] started
- [2026-10-25] implemented process settings singleton, chat-principal JOIN (4→1 SELECT), notification idle fast path (4→2 SELECT), admission materials dedupe; tests: console_platform+console_auth 476 passed, console_internal 60 passed, notifications 10-user self-check 2 passed
- [2026-10-25] completed (done)

---

## TASK-007: Connector 运行时与企微状态隔离

- **Status**: done
- **Priority**: P0
- **Depends**: TASK-006
- **Source**: high-qps-remediation.backend.design.md#3.2 架构设计, high-qps-remediation.backend.design.md#3.3 数据设计
- **Spec-Refs**: -
- **Acceptance-Refs**: S-05, E-03, E-04, E-06, E-07
- **Estimate**: 2d
- **Capability**: 企微长连接独立镜像；同 bot 单 owner，故障可接管，reply_ref 不串消息

### Description

新建 apps/im-connector；移动 WeCom adapter/reply/media/sdk；bot lease/fencing、reply store、inbound/outbound Stream、命令幂等。

### Checklist

- [x] 交付 `<Capability>`：双 Connector 只有一个 active owner，kill owner 可接管
- [x] 新增 `apps/im-connector` 包/镜像；WeCom 连接所有权可由 Connector 接管（Gateway 移除 WeCom 归 TASK-009 的部署切换，见 design §4.2；镜像 Dockerfile 已加，k8s 清单/预检归 TASK-009）
- [x] Redis lease + epoch；reply store（message/run/stream）；inbound/outbound Stream
- [x] 出站命令幂等/结果回写；epoch 不匹配降级主动发送
- [x] [S-05][integration] 假 WS 双 Connector：单 owner/接管/无重复
- [x] [E-03][integration] 平台重投：只建一个 Run
- [x] [E-07][integration] 平台不重投：显式 LOST/告警，不假装成功
- [x] [E-04][integration] reply_ref 过期：主动发送+DEGRADED，不串消息
- [ ] [E-06][manual] 真机企微双 WS/req_id 跨重连：记录真实语义（staging；owner=Connector 负责人；deadline=Phase 2 结束前；未通过不得宣称企微多连接安全）——**本轮未执行，保留 manual**
- [x] 运行验收命令并填写 Acceptance Evidence

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| S-05 | integration | 双 Connector 进程 + 假 WS + Redis lease | 单 owner；epoch 拒旧 owner；接管；无重复 | tests/acceptance/im_gateway/test_connector_lease.py | ["uv", "run", "pytest", "-q", "tests/acceptance/im_gateway/test_connector_lease.py"] | e2e_deferred |
| E-03 | integration | 真实 Connector + Redis Stream；平台重投 | 最终一个 Run；无重复回复 | tests/acceptance/im_gateway/test_connector_failover.py | ["uv", "run", "pytest", "-q", "tests/acceptance/im_gateway/test_connector_failover.py"] | e2e_deferred |
| E-04 | integration | 真实 Connector + 假 WS；旧 reply_ref | 主动发送+DEGRADED；不串消息 | tests/acceptance/im_gateway/test_connector_failover.py | ["uv", "run", "pytest", "-q", "tests/acceptance/im_gateway/test_connector_failover.py"] | verified |
| E-06 | manual | 真实企微 + 两个 Connector + staging | 记录双 WS/req_id 寿命；降级符合预期 | manual/staging（owner=Connector 负责人；deadline=Phase 2 结束前） | - | planned |
| E-07 | integration | 真实 Connector + Redis；平台不重投 | 显式 FAILED/告警；不承诺 exactly-once | tests/acceptance/im_gateway/test_connector_failover.py | ["uv", "run", "pytest", "-q", "tests/acceptance/im_gateway/test_connector_failover.py"] | e2e_deferred |

### Acceptance Evidence

> 编码期由 `cf-task-start` 填写 RED/GREEN、断言位置与真实边界证据；manual 场景登记原因/边界/验收方式。

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|---|---|---|---|---|---|
| S-05 | 两个 Connector 各自建连（无 lease 过滤）；接管窗口重复入站 | 两个真实 Connector 进程 + 真实 Redis：只有 1 条 `aibot_subscribe`；SIGKILL owner 后另一进程以更高 epoch 接管并新建连接；接管后同 message_id 只有 1 条 Stream 记录；reply store 跨进程可读 | `tests/acceptance/im_gateway/test_connector_lease.py::test_s05_single_owner_takeover_after_sigkill_and_no_duplicate_inbound` | 真实 Console 快照 + 真实 Redis lease/Stream + 真实本地 WSS 探针 | verified |
| E-03 | 收到消息后、XADD 前 SIGKILL：平台重投可能建两个 Run | 崩溃点确定性复现（RECEIVED 标记后暂停）；重启后平台重投发布一次；同一条入站事实提交 Runtime 两次仍只有 1 个 `run_record` | `tests/acceptance/im_gateway/test_connector_failover.py::test_e03_crash_before_xadd_then_platform_redelivery_creates_one_run` | 真实 Connector 子进程 + 真实 Redis Stream + 真实 Runtime/PG | verified |
| E-04 | reply_ref 过期仍用旧 req_id；或静默丢弃内容 | 有效 reply_ref 走 `aibot_respond_msg`；删除 reply store 后同命令返回 `DEGRADED/REPLY_REF_EXPIRED`，探针只收到 `aibot_send_msg`，没有任何帧携带旧 req_id | `tests/acceptance/im_gateway/test_connector_failover.py::test_e04_expired_reply_ref_degrades_to_active_without_wrong_callback` | 真实 Connector + 真实 Redis reply store + 真实 WSS 探针 | verified |
| E-06 | 未执行（需要真实企微 + staging 双 WS） | 未执行；已登记 manual/staging，owner=Connector 负责人，deadline=Phase 2 结束前 | manual | staging | manual |
| E-07 | 不重投时标记停在 RECEIVED，看起来像"没发生" | 重启扫描把超时 RECEIVED 置 LOST；`connector_inbound_lost_total` 增长；ERROR 日志可查；无 Stream 记录、无 Run | `tests/acceptance/im_gateway/test_connector_failover.py::test_e07_no_redelivery_is_explicitly_lost_and_never_fakes_a_run` | 真实 Connector 子进程 + 真实 Redis；不重投由测试显式不推送模拟 | verified |
- E-03: e2e_deferred — automated command e2e_deferred; run_id=8c14735dc81948b0a96011caaa0ac9c3 (confirmed_by: runner)
- E-04: verified — automated command passed; run_id=8c14735dc81948b0a96011caaa0ac9c3 (confirmed_by: runner)
- E-07: e2e_deferred — automated command e2e_deferred; run_id=8c14735dc81948b0a96011caaa0ac9c3 (confirmed_by: runner)
- S-05: e2e_deferred — automated command e2e_deferred; run_id=8c14735dc81948b0a96011caaa0ac9c3 (confirmed_by: runner)
- E-03: e2e_deferred — automated command e2e_deferred; run_id=8a4915ef786945a1a06c383376d7f958 (confirmed_by: runner)
- E-07: e2e_deferred — automated command e2e_deferred; run_id=8a4915ef786945a1a06c383376d7f958 (confirmed_by: runner)
- S-05: e2e_deferred — automated command e2e_deferred; run_id=8a4915ef786945a1a06c383376d7f958 (confirmed_by: runner)

### Log

- [2026-10-25] created (draft)
- [2026-10-25] implemented `apps/im-connector` (lease/reply store/inbound/outbound/WeCom runtime) + Connector process; S-05/E-03/E-04/E-07 pass; E-06 stays manual
- [2026-10-25] fixed TASK-003 regression exposed by the full IM suite: SSE routes released the request Session before the replay generator read ORM attributes → `DetachedInstanceError`; `_replay_events` now captures plain values before release
- [2026-10-25] started
- [2026-10-25] completed (done)

---

## TASK-008: Gateway 无状态、投递 facade 与 Webhook

- **Status**: done
- **Priority**: P0
- **Depends**: TASK-006
- **Source**: high-qps-remediation.backend.design.md#3.4 接口设计, high-qps-remediation.backend.design.md#3.5 质量实现方案
- **Spec-Refs**: harness-api#RULE-api-001, harness-api#RULE-api-002, harness-secret#RULE-secret-001
- **Acceptance-Refs**: S-07
- **Estimate**: 1.5d
- **Capability**: Gateway 只做无状态请求面；Webhook 只加 adapter 即可接入；投递按 bot 路由不落错 Pod

### Description

移除 WeCom、delivery facade 发 im.outbound、bot snapshot 按 channel 过滤、Webhook route 验签/入队/ACK。

### Checklist

- [x] 交付 `<Capability>`：Gateway 无 WeCom SDK/secret；Webhook 合法事件入队、非法签名拒绝
- [x] 实现 delivery facade：校验→im.outbound→有界等待；WEBCHAT 走 inbox/SSE
- [x] bot snapshot 按 channel 过滤；Gateway 不接收 WECOM secret
- [x] 新增 `/channels/{channel}/events` Webhook 路由：验签→XADD→快速 2xx
- [x] [S-07][integration] Webhook adapter 接入、非法签名拒绝、重复 event_id 幂等
- [x] [harness-api#RULE-api-001][verifier] uv run pytest -q tests/test_api_i18n.py tests/test_error_catalog.py tests/acceptance/test_foundation_api_envelope.py
- [x] [harness-api#RULE-api-002][verifier] uv run pytest -q tests/console_skill/test_import_idempotency.py
- [x] [harness-secret#RULE-secret-001][verifier] uv run pytest -q tests/test_logging_redaction.py tests/acceptance/test_foundation_ops_audit.py
- [x] 运行验收命令并填写 Acceptance Evidence

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| S-07 | integration | 真实 Gateway HTTP + 假 webhook + Redis Stream + Runtime | 不新增 Deployment；合法先持久化再 ACK；非法签名不落库；重复 event_id 幂等 | tests/acceptance/im_gateway/test_webhook_channel.py | ["uv", "run", "pytest", "-q", "tests/acceptance/im_gateway/test_webhook_channel.py"] | verified |

**Spec Verifier**

| Rule | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 执行命令 | 状态 |
|---|---|---|---|---|---|
| harness-api#RULE-api-001 | integration | 真实 PG/Redis/HTTP/architecture tests；不 mock 规则边界 | 规则既有 verifier 全绿；本任务不改变其语义 | uv run pytest -q tests/test_api_i18n.py tests/test_error_catalog.py tests/acceptance/test_foundation_api_envelope.py | verified |
| harness-api#RULE-api-002 | integration | 真实 PG/Redis/HTTP/architecture tests；不 mock 规则边界 | 规则既有 verifier 全绿；本任务不改变其语义 | uv run pytest -q tests/console_skill/test_import_idempotency.py | verified |
| harness-secret#RULE-secret-001 | integration | 真实 PG/Redis/HTTP/architecture tests；不 mock 规则边界 | 规则既有 verifier 全绿；本任务不改变其语义 | uv run pytest -q tests/test_logging_redaction.py tests/acceptance/test_foundation_ops_audit.py | verified |

### Acceptance Evidence

> 编码期由 `cf-task-start` 填写 RED/GREEN、断言位置与真实边界证据；manual 场景登记原因/边界/验收方式。

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|---|---|---|---|---|---|
| S-07 | `uv run pytest -q tests/acceptance/im_gateway/test_webhook_channel.py` → 4 failed / 1 passed：四个 HTTP 用例全部 404 `COMMON_NOT_FOUND`（`/channels/WECOM/events` 路由尚不存在），静态「不新增 Deployment」用例通过 | 同一命令 → 5 passed：合法 HMAC 事件 202 且 ACK 前已可从 Redis 回读 `im.inbound:*` 条目，随后真实 Runtime 建 Run 到终态；错签名/过期时间戳 403 且零条目、零新 Run；重复 event_id 第二次 `duplicate=true`、仍只有 1 条 Stream 记录、Run 计数不变 | `tests/acceptance/im_gateway/test_webhook_channel.py::test_s07_valid_event_is_persisted_before_ack_and_creates_one_run`、`::test_s07_invalid_signature_is_rejected_without_persisting_or_running`、`::test_s07_expired_timestamp_is_rejected_without_persisting`、`::test_s07_duplicate_event_id_is_idempotent`、`::test_s07_no_new_deployment_or_image_is_required`；单元面 `tests/gateway/test_webhook_channel.py`（验签/时间戳/载荷/配置装配）与 `tests/gateway/test_delivery_stream.py`（入队+有界等待） | 真实 uvicorn Gateway 子进程 + 真实 Console/Runtime/Worker + 真实 `wss`-free webhook HTTP 客户端（HMAC）+ 真实 Redis `im.inbound:*` + 真实 PostgreSQL `runtime.run_record`；`deploy/k8s/base` 无 webhook 专属 Deployment。inbound 消费组 `im-ingress` 落 `apps/im-gateway/src/muad_im_gateway/application/inbound_streams.py`，验签/入队在 `channels/webhook.py` 与 `api/webhook.py` | verified |
- S-07: verified — automated command passed; run_id=9503f100ec844aafb0b4239212d5784d (confirmed_by: runner)

### Log

- [2026-10-25] created (draft)
- [2026-10-25] started
- [2026-10-25] implemented short SSE session (`release_session` before StreamingResponse), `get_session` in_transaction commit, stream-scope gate sized below pool; S-03 100 streams pass, E-02 pool timeout bounded; 7 stream tests passed
- [2026-10-25] started
- [2026-10-25] implemented process settings singleton, chat-principal JOIN (4→1 SELECT), notification idle fast path (4→2 SELECT), admission materials dedupe; tests: console_platform+console_auth 476 passed, console_internal 60 passed, notifications 10-user self-check 2 passed
- [2026-10-25] completed (done)

---

## TASK-009: Gateway/Connector 切换、预检与多 worker 边界

- **Status**: draft
- **Priority**: P0
- **Depends**: TASK-007, TASK-008
- **Source**: high-qps-remediation.backend.design.md#4.1 部署架构, high-qps-remediation.backend.design.md#4.1.1 必须同步的 spec / architecture tests / preflight
- **Spec-Refs**: harness-arch#RULE-arch-001, harness-im#RULE-im-001, harness-im#RULE-im-002
- **Acceptance-Refs**: S-06, S-12, B-03
- **Estimate**: 2d
- **Capability**: 多 Gateway 副本 + Connector 可部署；任意 Pod 可服务/重连；5 单元预检通过

### Description

Gateway 移除 WeCom、多副本/HPA；Connector 清单/PDB/lease；预检支持 5 单元/多 worker/每单元池；架构测试更新。

### Checklist

- [ ] 交付 `<Capability>`：两个 Gateway 副本 + 一个 Connector，WebChat 任意 Pod 可服务/重连
- [ ] 更新 preflight：单元集合由 profile 声明、process_counts 乘预算、每单元 DB 池、Gateway 多副本
- [ ] 更新 architecture tests：SDK 允许 connector surface、Gateway 无 WeCom、Connector metrics/probe
- [ ] [S-06][E2E] 多 Gateway 提交/SSE 重连/同 message_id 重试/Pod 重启
- [ ] [S-12][integration] 独立镜像、无公开路由、无 WeCom SDK、ChannelName 开放、5 单元预检
- [ ] [B-03][integration] 多 worker 指标聚合、artifact token 跨 worker
- [ ] [harness-arch#RULE-arch-001][verifier] uv run pytest -q tests/architecture
- [ ] [harness-im#RULE-im-001][verifier] uv run pytest -q tests/console_channel tests/gateway
- [ ] [harness-im#RULE-im-002][verifier] uv run pytest -q tests/architecture/test_channel_neutrality.py
- [x] 运行验收命令并填写 Acceptance Evidence

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| S-06 | E2E | 真实浏览器/HTTP + 两 Gateway 副本 + Runtime/PG/Redis | 任意 Pod 服务；游标续流无缺口；同 message_id 一 Run；Pod 重启不丢 | e2e/.../capacity-recovery.spec.ts | ["bash", "-lc", "npm --prefix apps/console-platform/frontend run build && npm --prefix e2e test -- --config playwright.webchat.config.ts capacity-recovery.spec.ts"] | planned |
| S-12 | integration | 真实包导入/清单渲染/architecture tests | Gateway 无 SDK/secret；Connector 无公开路由；5 单元预检通过 | tests/architecture | ["uv", "run", "pytest", "-q", "tests/architecture"] | planned |
| B-03 | integration | 真实 uvicorn 2+ workers；metrics/artifact token | 指标含所有 worker；token 跨 worker；无 404/漏计数 | tests/architecture/test_multi_worker_state.py | ["uv", "run", "pytest", "-q", "tests/architecture/test_multi_worker_state.py"] | planned |

**Spec Verifier**

| Rule | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 执行命令 | 状态 |
|---|---|---|---|---|---|
| harness-arch#RULE-arch-001 | integration | 真实 PG/Redis/HTTP/architecture tests；不 mock 规则边界 | 规则既有 verifier 全绿；本任务不改变其语义 | uv run pytest -q tests/architecture | planned |
| harness-im#RULE-im-001 | integration | 真实 PG/Redis/HTTP/architecture tests；不 mock 规则边界 | 规则既有 verifier 全绿；本任务不改变其语义 | uv run pytest -q tests/console_channel tests/gateway | planned |
| harness-im#RULE-im-002 | integration | 真实 PG/Redis/HTTP/architecture tests；不 mock 规则边界 | 规则既有 verifier 全绿；本任务不改变其语义 | uv run pytest -q tests/architecture/test_channel_neutrality.py | planned |

### Acceptance Evidence

> 编码期由 `cf-task-start` 填写 RED/GREEN、断言位置与真实边界证据；manual 场景登记原因/边界/验收方式。

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|---|---|---|---|---|---|
| S-06 | planned | planned | planned | planned | planned |
| S-12 | planned | planned | planned | planned | planned |
| B-03 | planned | planned | planned | planned | planned |

### Log

- [2026-10-25] created (draft)
- [2026-10-25] started
- [2026-10-25] implemented short SSE session (`release_session` before StreamingResponse), `get_session` in_transaction commit, stream-scope gate sized below pool; S-03 100 streams pass, E-02 pool timeout bounded; 7 stream tests passed
- [2026-10-25] started
- [2026-10-25] implemented process settings singleton, chat-principal JOIN (4→1 SELECT), notification idle fast path (4→2 SELECT), admission materials dedupe; tests: console_platform+console_auth 476 passed, console_internal 60 passed, notifications 10-user self-check 2 passed

---

## TASK-010: WebChat 统一核心与读路径

- **Status**: draft
- **Priority**: P0
- **Depends**: TASK-006, TASK-002
- **Source**: high-qps-remediation.backend.design.md#3.2 架构设计, high-qps-remediation.backend.design.md#3.5 质量实现方案
- **Spec-Refs**: -
- **Acceptance-Refs**: S-08, S-15
- **Estimate**: 1.5d
- **Capability**: WebChat 走统一 SYNC 核心且 202 不回归；打开流/202 不再读完整 snapshot

### Description

WebChat submit 走 admit_sync（复用 ChatIdentity）；lean scope；受理帧带 status；去掉 202 后 get_run；清理 dead queue。

### Checklist

- [ ] 交付 `<Capability>`：同 message_id 只建一个 Run；scope≤2 SQL；202 后无 get_run
- [ ] 实现 WebChat SYNC 核心入口，复用已验证 principal，不重复 Console resolve
- [ ] lean `get_run_scope`；受理帧带 status/execution_status；移除 dead WebChat queue
- [ ] [S-08][integration] SYNC 受理/幂等/202 P95≤500ms/不重复 Console resolve
- [ ] [S-15][integration] scope≤2 SQL、202 不 get_run、dead queue 清理
- [x] 运行验收命令并填写 Acceptance Evidence

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| S-08 | integration | 真实 Gateway/Console/Runtime/PG | 一 Run；202 P95≤500ms；不重复 resolve | tests/acceptance/im_gateway/test_webchat_core.py | ["uv", "run", "pytest", "-q", "tests/acceptance/im_gateway/test_webchat_core.py"] | planned |
| S-15 | integration | 真实 Gateway/Runtime/PG；SQL 计数 | scope≤2 SQL；202 后无 get_run；无 dead queue | tests/agent_runtime/test_runs_api.py | ["uv", "run", "pytest", "-q", "tests/agent_runtime/test_runs_api.py"] | planned |

### Acceptance Evidence

> 编码期由 `cf-task-start` 填写 RED/GREEN、断言位置与真实边界证据；manual 场景登记原因/边界/验收方式。

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|---|---|---|---|---|---|
| S-08 | planned | planned | planned | planned | planned |
| S-15 | planned | planned | planned | planned | planned |

### Log

- [2026-10-25] created (draft)
- [2026-10-25] started
- [2026-10-25] implemented short SSE session (`release_session` before StreamingResponse), `get_session` in_transaction commit, stream-scope gate sized below pool; S-03 100 streams pass, E-02 pool timeout bounded; 7 stream tests passed
- [2026-10-25] started
- [2026-10-25] implemented process settings singleton, chat-principal JOIN (4→1 SELECT), notification idle fast path (4→2 SELECT), admission materials dedupe; tests: console_platform+console_auth 476 passed, console_internal 60 passed, notifications 10-user self-check 2 passed

---

## TASK-011: 前端通知 page_size、轮询与 code splitting

- **Status**: draft
- **Priority**: P1
- **Depends**: TASK-002
- **Source**: high-qps-remediation.frontend.design.md#2.2 功能方案, high-qps-remediation.backend.design.md#3.5 质量实现方案
- **Spec-Refs**: harness-frontend#RULE-front-001, harness-ui#RULE-ui-001
- **Acceptance-Refs**: S-09, S-10, B-04
- **Estimate**: 1.5d
- **Capability**: 通知轮询不再回源设置；聊天首屏不加载 admin chunk；静态 overlay 同源可用（可选）

### Description

useNotifications 带 page_size、轮询 15s；admin 路由 lazy；可选 console-web overlay 同源路径。

### Checklist

- [ ] 交付 `<Capability>`：请求带 page_size；聊天首屏不含 admin chunk；静态 overlay 同源
- [ ] `useNotifications` 从 client-config 取 page_size/poll_sec；service 层请求
- [ ] admin 路由 lazy + Suspense/错误边界；深链可用
- [ ] 可选 console-web overlay：nginx 静态 + Ingress 同源路径；默认 base 仍 4 单元
- [ ] [S-09][component] 请求 query/page_size/轮询间隔/不裸 fetch
- [ ] [S-10][component] admin lazy/深链/聊天首屏不加载 admin chunk
- [ ] [B-04][E2E] 静态 overlay 同源 Cookie/CSRF/SSE；默认 base 对照
- [ ] [harness-frontend#RULE-front-001][verifier] bash -lc "uv run pytest -q tests/frontend/test_api_client_contract.py && uv run python scripts/check_frontend_api_usage.py && uv run python scripts/check_frontend_i18n.py && npm --prefix apps/console-platform/frontend run typecheck"
- [ ] [harness-ui#RULE-ui-001][verifier] bash -lc "uv run pytest -q tests/frontend/test_console_shell_contract.py tests/frontend/test_ui_style_contract.py && npm --prefix apps/console-platform/frontend run build"
- [x] 运行验收命令并填写 Acceptance Evidence

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| S-09 | component | 前端 service/hook + mock HTTP | page_size/轮询间隔来自投影；不裸 fetch/axios | tests/frontend/test_capacity_webchat_activity_ui.py | ["uv", "run", "pytest", "-q", "tests/frontend/test_capacity_webchat_activity_ui.py"] | planned |
| S-10 | component | 构建产物 + 前端路由（mock API） | 聊天首屏无 admin chunk；深链可用；菜单 11 项 | tests/frontend/test_webchat_code_splitting.py | ["uv", "run", "pytest", "-q", "tests/frontend/test_webchat_code_splitting.py"] | planned |
| B-04 | E2E | 真实浏览器 + Ingress + console-web/api/gateway 同源 | Cookie/CSRF/Origin 正常；静态不经过 Python；SSE buffering off | e2e/.../static-overlay.spec.ts | ["bash", "-lc", "npm --prefix apps/console-platform/frontend run build && npm --prefix e2e test -- --config playwright.webchat.config.ts static-overlay.spec.ts"] | planned |

**Spec Verifier**

| Rule | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 执行命令 | 状态 |
|---|---|---|---|---|---|
| harness-frontend#RULE-front-001 | integration | 真实 PG/Redis/HTTP/architecture tests；不 mock 规则边界 | 规则既有 verifier 全绿；本任务不改变其语义 | bash -lc "uv run pytest -q tests/frontend/test_api_client_contract.py && uv run python scripts/check_frontend_api_usage.py && uv run python scripts/check_frontend_i18n.py && npm --prefix apps/console-platform/frontend run typecheck" | planned |
| harness-ui#RULE-ui-001 | integration | 真实 PG/Redis/HTTP/architecture tests；不 mock 规则边界 | 规则既有 verifier 全绿；本任务不改变其语义 | bash -lc "uv run pytest -q tests/frontend/test_console_shell_contract.py tests/frontend/test_ui_style_contract.py && npm --prefix apps/console-platform/frontend run build" | planned |

### Acceptance Evidence

> 编码期由 `cf-task-start` 填写 RED/GREEN、断言位置与真实边界证据；manual 场景登记原因/边界/验收方式。

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|---|---|---|---|---|---|
| S-09 | planned | planned | planned | planned | planned |
| S-10 | planned | planned | planned | planned | planned |
| B-04 | planned | planned | planned | planned | planned |

### Log

- [2026-10-25] created (draft)
- [2026-10-25] started
- [2026-10-25] implemented short SSE session (`release_session` before StreamingResponse), `get_session` in_transaction commit, stream-scope gate sized below pool; S-03 100 streams pass, E-02 pool timeout bounded; 7 stream tests passed
- [2026-10-25] started
- [2026-10-25] implemented process settings singleton, chat-principal JOIN (4→1 SELECT), notification idle fast path (4→2 SELECT), admission materials dedupe; tests: console_platform+console_auth 476 passed, console_internal 60 passed, notifications 10-user self-check 2 passed

---

## TASK-012: 全量验收、混沌与发布门禁

- **Status**: draft
- **Priority**: P0
- **Depends**: TASK-001, TASK-003, TASK-004, TASK-005, TASK-009, TASK-010, TASK-011
- **Source**: high-qps-remediation.backend.design.md#2.5 验收条件, high-qps-remediation.backend.design.md#4.2 发布与回滚
- **Spec-Refs**: harness-test#RULE-test-001
- **Acceptance-Refs**: S-01, E-08, E-09
- **Estimate**: 2d
- **Capability**: 1000 用户真实流式/浏览器同形负载达到 SLO；故障/混沌可恢复；manual release gate 可执行

### Description

把 1000×8 全量、PG failover/Redis 抖动/Pod kill、真机企微 manual gate 串成发布前验收；不以 6 用户 report 冒充。

### Checklist

- [ ] 交付 `<Capability>`：1000×8 capacity_load 产出 SLO/PG/CPU/retention 证据；混沌通过；manual gate 有 owner/截止
- [ ] 执行 S-01 全量：attempts=8000、steady 600s、SLO/PG/CPU/retention 断言
- [ ] 执行 E-08 混沌：PG failover、Redis 抖动、Gateway/Runtime Pod kill；已受理不丢
- [ ] 登记 E-09 manual/staging release gate：owner/截止/证据；未过不得写达标
- [ ] [S-01][integration] 1000×8 全量 capacity_load；SLO/PG/CPU/retention
- [ ] [E-08][integration] 混沌恢复；无重复终态/投递；降级可见
- [ ] [E-09][manual] staging 1000×8 人工 release gate（owner=发布负责人；deadline=发布前；未过不得写生产达标）
- [ ] [harness-test#RULE-test-001][verifier] bash -lc "uv run pytest -q tests/acceptance && npm --prefix apps/console-platform/frontend run build && npm --prefix e2e test"
- [x] 运行验收命令并填写 Acceptance Evidence

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| S-01 | integration | 真实目标拓扑 + 1000×8 + 20 chunks/s + 15s 通知 | SLO 全过；attempts=8000；PG/CPU/retention 预算内 | tests/acceptance/capacity/test_capacity.py | ["bash", "-lc", "uv run pytest -q -m capacity_load tests/acceptance/capacity/test_capacity.py"] | planned |
| E-08 | integration | 真实 PG/Redis + Pod kill/failover | 已受理不丢；SSE 可恢复；无重复终态/投递 | tests/acceptance/capacity/test_capacity_chaos.py | ["uv", "run", "pytest", "-q", "tests/acceptance/capacity/test_capacity_chaos.py"] | planned |
| E-09 | manual | staging 目标拓扑 + 人工执行 | SLO 全过并记录证据；未过不得写达标 | manual/staging（owner=发布负责人；deadline=发布前） | - | planned |

**Spec Verifier**

| Rule | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 执行命令 | 状态 |
|---|---|---|---|---|---|
| harness-test#RULE-test-001 | integration | 真实 PG/Redis/HTTP/architecture tests；不 mock 规则边界 | 规则既有 verifier 全绿；本任务不改变其语义 | bash -lc "uv run pytest -q tests/acceptance && npm --prefix apps/console-platform/frontend run build && npm --prefix e2e test" | planned |

### Acceptance Evidence

> 编码期由 `cf-task-start` 填写 RED/GREEN、断言位置与真实边界证据；manual 场景登记原因/边界/验收方式。

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|---|---|---|---|---|---|
| S-01 | planned | planned | planned | planned | planned |
| E-08 | planned | planned | planned | planned | planned |
| E-09 | planned | planned | planned | planned | planned |

### Log

- [2026-10-25] created (draft)
- [2026-10-25] started
- [2026-10-25] implemented short SSE session (`release_session` before StreamingResponse), `get_session` in_transaction commit, stream-scope gate sized below pool; S-03 100 streams pass, E-02 pool timeout bounded; 7 stream tests passed
- [2026-10-25] started
- [2026-10-25] implemented process settings singleton, chat-principal JOIN (4→1 SELECT), notification idle fast path (4→2 SELECT), admission materials dedupe; tests: console_platform+console_auth 476 passed, console_internal 60 passed, notifications 10-user self-check 2 passed

---

*文档结束*
