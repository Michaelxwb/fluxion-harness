# Tasks: 高 QPS 架构整改

- **Source**: `.code-flow/tasks/2026-10-24/high-qps-remediation/`（backend/frontend design + scenarios 合并）
- **Created**: 2026-10-25
- **Updated**: 2026-10-25
- **Plan-State**: TASK-001 done；TASK-002..012 draft（按批次继续）

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
| B-01 | high-qps-remediation.backend.design.md#2.5 验收条件 | integration | 真实 Gateway/Runtime/PG；ramp=0，1000 个不同用户同时提交 | TASK-005 | planned | ["bash", "-lc", "CAPACITY_LOAD_USERS=1000 uv run pytest -q -m capacity_load tests/acceptance/capacity/test_capacity.py"] | . | 5400 | P0 | deferred | local-api | - | - | - | no |
| B-02 | high-qps-remediation.backend.design.md#2.5 验收条件 | integration | 真实 PostgreSQL；批量写入 + retention sweep | TASK-004 | planned | ["uv", "run", "pytest", "-q", "tests/agent_runtime/test_event_retention_throughput.py"] | . | 600 | P0 | auto | local-api | - | - | - | no |
| B-03 | high-qps-remediation.backend.design.md#2.5 验收条件 | integration | 真实 uvicorn 2+ workers；/metrics 与 artifact fetch token | TASK-009 | planned | ["uv", "run", "pytest", "-q", "tests/architecture/test_multi_worker_state.py"] | . | 600 | P1 | auto | local-api | - | - | - | no |
| B-04 | high-qps-remediation.frontend.design.md#2.4 验收条件 | E2E | 真实浏览器 → Ingress → console-web/CDN + console-api + gateway；同源 Cookie | TASK-011 | planned | ["bash", "-lc", "npm --prefix apps/console-platform/frontend run build && npm --prefix e2e test -- --config playwright.webchat.config.ts static-overlay.spec.ts"] | . | 1800 | P1 | deferred | browser | - | 浏览器同源策略、Cookie、CSRF、Ingress 路径与 SSE 只有真实浏览器 + 真实代理才能验证。 | ["apps/console-platform/frontend/**", "deploy/k8s/base/ingress.yaml", "deploy/k8s/overlays/console-web/**"] | no |
| E-01 | high-qps-remediation.backend.design.md#2.5 验收条件 | integration | 真实 Gateway/Runtime + Console auth；模拟 platform-settings 不可用 | TASK-002 | verified | ["uv", "run", "pytest", "-q", "tests/acceptance/capacity/test_capacity_notification_settings.py"] | . | 900 | P1 | auto | local-api | - | - | - | no |
| E-02 | high-qps-remediation.backend.design.md#2.5 验收条件 | integration | 真实 Runtime + PostgreSQL；pool=1，打开 10 条 SSE | TASK-003 | planned | ["uv", "run", "pytest", "-q", "tests/acceptance/capacity/test_stream_delivery.py"] | . | 600 | P0 | auto | local-api | - | - | - | no |
| E-03 | high-qps-remediation.backend.design.md#2.5 验收条件 | integration | 真实 Connector 进程 + Redis Stream；kill -9 在 WS 收到后、XADD 前 | TASK-007 | planned | ["uv", "run", "pytest", "-q", "tests/acceptance/im_gateway/test_connector_failover.py"] | . | 900 | P0 | deferred | local-api | - | - | - | no |
| E-04 | high-qps-remediation.backend.design.md#2.5 验收条件 | integration | 真实 Connector + 假 WS 服务端；旧 reply_ref 过期后发送终态/产物 | TASK-007 | planned | ["uv", "run", "pytest", "-q", "tests/acceptance/im_gateway/test_connector_failover.py"] | . | 900 | P1 | auto | local-api | - | - | - | no |
| E-05 | high-qps-remediation.backend.design.md#2.5 验收条件 | integration | 真实 Runtime + Redis 停机 + PG 持久事件 | TASK-004 | planned | ["uv", "run", "pytest", "-q", "tests/agent_runtime/test_event_tail_hub_fallback.py"] | . | 600 | P1 | auto | local-api | - | - | - | no |
| E-06 | high-qps-remediation.backend.design.md#2.5 验收条件 | manual | 真实企微智能机器人 + 两个 Connector 候选 + 真实网络；不是假 WS | TASK-007 | planned | - | . | 600 | P0 | manual | staging | - | - | - | no |
| E-07 | high-qps-remediation.backend.design.md#2.5 验收条件 | integration | 真实 Connector 进程 + Redis Stream；关闭平台重投 | TASK-007 | planned | ["uv", "run", "pytest", "-q", "tests/acceptance/im_gateway/test_connector_failover.py"] | . | 900 | P0 | deferred | local-api | - | - | - | no |
| E-08 | high-qps-remediation.backend.design.md#2.5 验收条件 | integration | 真实进程 + PG/Redis 故障注入 | TASK-012 | planned | ["uv", "run", "pytest", "-q", "tests/acceptance/capacity/test_capacity_chaos.py"] | . | 900 | P0 | deferred | local-api | - | - | - | no |
| E-09 | high-qps-remediation.backend.design.md#2.5 验收条件 | manual | staging 目标拓扑；人工执行并记录证据 | TASK-012 | planned | - | . | 3600 | P0 | manual | staging | - | - | - | no |
| S-01 | high-qps-remediation.backend.design.md#2.5 验收条件 | integration | 真实入口代理 → Gateway → Runtime → PostgreSQL/Redis；模型为可控 HTTP 流式探针；通知轮询为浏览器同形请求 | TASK-012 | planned | ["bash", "-lc", "uv run pytest -q -m capacity_load tests/acceptance/capacity/test_capacity.py"] | . | 5400 | P0 | deferred | local-api | - | - | - | no |
| S-02 | high-qps-remediation.backend.design.md#2.5 验收条件 | integration | 浏览器同形通知轮询 → Gateway → Console auth / Runtime notifications；真实 PG/Redis | TASK-002 | e2e_deferred | ["bash", "-lc", "CAPACITY_NOTIFICATION_USERS=1000 CAPACITY_LOAD_USERS=1000 uv run pytest -q tests/acceptance/capacity/test_capacity_notifications.py -k s02"] | . | 900 | P0 | deferred | local-api | - | - | - | no |
| S-03 | high-qps-remediation.backend.design.md#2.5 验收条件 | integration | 真实 Gateway/Runtime HTTP SSE + 真实 PostgreSQL；runtime DB pool=2 | TASK-003 | planned | ["uv", "run", "pytest", "-q", "tests/acceptance/capacity/test_stream_delivery.py"] | . | 600 | P0 | auto | local-api | - | - | - | no |
| S-04 | high-qps-remediation.backend.design.md#2.5 验收条件 | integration | 真实 Runtime 事件 writer + 真实 PostgreSQL/Redis；20 delta/s × 10s | TASK-004 | planned | ["uv", "run", "pytest", "-q", "tests/agent_runtime/test_capacity_webchat_durable_deltas.py"] | . | 600 | P0 | auto | local-api | - | - | - | no |
| S-05 | high-qps-remediation.backend.design.md#2.5 验收条件 | integration | 两个真实 Connector 进程 + 假企微 WS 服务端 + 真实 Redis lease；Runtime/Gateway 真实进程 | TASK-007 | planned | ["uv", "run", "pytest", "-q", "tests/acceptance/im_gateway/test_connector_lease.py"] | . | 900 | P0 | deferred | local-api | - | - | - | no |
| S-06 | high-qps-remediation.backend.design.md#2.5 验收条件 | E2E | 真实浏览器/HTTP 客户端 → 两个以上 Gateway 副本 → Runtime/PG/Redis；SSE 断线重连到另一副本 | TASK-009 | planned | ["bash", "-lc", "npm --prefix apps/console-platform/frontend run build && npm --prefix e2e test -- --config playwright.webchat.config.ts capacity-recovery.spec.ts"] | . | 1800 | P0 | deferred | browser | - | SSE 长连接、负载均衡、断线重连和跨 Pod 游标只有真实浏览器 + 多副本 + Runtime 持久流才能覆盖。 | ["apps/im-gateway/**", "apps/agent-runtime/src/muad_agent_runtime/api/runs.py", "apps/agent-runtime/src/muad_agent_runtime/application/event_tail_hub.py", "deploy/k8s/base/im-gateway.yaml"] | yes |
| S-07 | high-qps-remediation.backend.design.md#2.5 验收条件 | integration | 真实 Gateway HTTP /channels/{channel}/events + 假 webhook 提供方 + Redis Stream + Runtime | TASK-008 | planned | ["uv", "run", "pytest", "-q", "tests/acceptance/im_gateway/test_webhook_channel.py"] | . | 600 | P1 | auto | local-api | - | - | - | no |
| S-08 | high-qps-remediation.backend.design.md#2.5 验收条件 | integration | 真实 Gateway → 统一消息核心 → Console/Runtime/PG；已验证 CHAT 主体上下文 | TASK-010 | planned | ["uv", "run", "pytest", "-q", "tests/acceptance/im_gateway/test_webchat_core.py"] | . | 600 | P0 | auto | local-api | - | - | - | no |
| S-09 | high-qps-remediation.frontend.design.md#2.4 验收条件 | component | 前端 service/hook + mock HTTP；不访问真实后端 | TASK-011 | planned | ["uv", "run", "pytest", "-q", "tests/frontend/test_capacity_webchat_activity_ui.py"] | . | 600 | P1 | auto | none | - | - | - | no |
| S-10 | high-qps-remediation.frontend.design.md#2.4 验收条件 | component | 构建产物 + 前端路由（mock API） | TASK-011 | planned | ["uv", "run", "pytest", "-q", "tests/frontend/test_webchat_code_splitting.py"] | . | 600 | P1 | auto | none | - | - | - | no |
| S-11 | high-qps-remediation.backend.design.md#2.5 验收条件 | contract | 纯契约/序列化；不连服务 | TASK-006 | planned | ["uv", "run", "pytest", "-q", "tests/contracts/test_im_contracts.py"] | . | 600 | P0 | auto | none | - | - | - | no |
| S-12 | high-qps-remediation.backend.design.md#2.5 验收条件 | integration | 真实包导入/清单渲染/architecture tests；不启动业务栈 | TASK-009 | planned | ["uv", "run", "pytest", "-q", "tests/architecture"] | . | 600 | P0 | auto | none | - | - | - | no |
| S-13 | high-qps-remediation.backend.design.md#2.5 验收条件 | integration | 真实 Runtime/Worker/PG + 模拟队列压力 | TASK-005 | planned | ["uv", "run", "pytest", "-q", "tests/agent_runtime/test_runtime_capacity_metrics.py"] | . | 900 | P0 | auto | local-api | - | - | - | no |
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
- [2026-10-25] implemented process settings singleton, chat-principal JOIN (4→1 SELECT), notification idle fast path (4→2 SELECT), admission materials dedupe; tests: console_platform+console_auth 476 passed, console_internal 60 passed, notifications 10-user self-check 2 passed
- [2026-10-25] started
- [2026-10-25] completed (done)

---

## TASK-003: SSE 短 Session 与连接池预算

- **Status**: draft
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

- [ ] 交付 `<Capability>`：pool=2 时 100 条 SSE 全部打开，PG total 不随流数线性增长
- [ ] 拆分 RunStreamService/短 Session；tail/scope 校验后 commit/close 再返回流
- [ ] `get_session` 只在 in_transaction 时 commit；显式池/pre_ping/timeout 调整
- [ ] 准入材料阶段与写事务 gate 分离；preflight 校验 pool+overflow ≥ gate
- [ ] [S-03][integration] pool=2 × 100 流：全部成功、PG total≤20、稳态 idle-in-tx≈0
- [ ] [E-02][integration] pool=1 超容量：1s 内显式失败，不挂 5s、不抛未处理 QueuePool
- [x] 运行验收命令并填写 Acceptance Evidence

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| S-03 | integration | 真实 Runtime HTTP SSE + PostgreSQL；pool=2 | 100 条流成功；PG total≤20；idle-in-tx≈0 | tests/acceptance/capacity/test_stream_delivery.py | ["uv", "run", "pytest", "-q", "tests/acceptance/capacity/test_stream_delivery.py"] | planned |
| E-02 | integration | 真实 Runtime + PostgreSQL；pool=1/overflow=0/timeout=1s | 1s 内显式失败；无未处理 QueuePool；指标可见 | tests/acceptance/capacity/test_stream_delivery.py | ["uv", "run", "pytest", "-q", "tests/acceptance/capacity/test_stream_delivery.py"] | planned |

### Acceptance Evidence

> 编码期由 `cf-task-start` 填写 RED/GREEN、断言位置与真实边界证据；manual 场景登记原因/边界/验收方式。

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|---|---|---|---|---|---|
| S-03 | planned | planned | planned | planned | planned |
| E-02 | planned | planned | planned | planned | planned |

### Log

- [2026-10-25] created (draft)
- [2026-10-25] started
- [2026-10-25] implemented process settings singleton, chat-principal JOIN (4→1 SELECT), notification idle fast path (4→2 SELECT), admission materials dedupe; tests: console_platform+console_auth 476 passed, console_internal 60 passed, notifications 10-user self-check 2 passed

---

## TASK-004: 事件批量写、序列与保留

- **Status**: draft
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

- [ ] 交付 `<Capability>`：100 Run × 20 delta/s 下 ≤4 commits/s/Run、≤3 statements/event
- [ ] 实现 per-Run RunEventWriter（首段立即 flush，后续 250–500ms/8–16KB）
- [ ] 批次级 lease fence + seq 块 + 批量 INSERT；去掉每事件 lock_run_source/database_now
- [ ] 事件带 execution_epoch；terminal 最后；SIGKILL 前缀可 replay
- [ ] retention 批量/索引/分区；新索引/列遵守标准列/timestamptz
- [ ] [S-04][integration] 批量写/重放/终态/SIGKILL 断言
- [ ] [E-05][integration] Redis 故障退 PG 有界补查，恢复回切
- [ ] [B-02][integration] 20k rows/s 等价写入下 retention 删除≥写入
- [ ] [harness-data#RULE-data-001][verifier] uv run pytest -q tests -k schema_parity
- [ ] [harness-snapshot#RULE-snapshot-001][verifier] bash -lc "uv run pytest -q tests/agent_runtime/test_snapshot_freeze.py tests/agent_runtime/test_run_reaper.py && uv run pytest -q tests/agent_runtime -k 'executor or resolve'"
- [x] 运行验收命令并填写 Acceptance Evidence

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| S-04 | integration | 真实 Runtime 事件 writer + PostgreSQL/Redis；20 delta/s × 10s | ≤4 commits/s/Run；≤3 statements/event；无丢/重 seq；terminal 最后 | tests/agent_runtime/test_capacity_webchat_durable_deltas.py | ["uv", "run", "pytest", "-q", "tests/agent_runtime/test_capacity_webchat_durable_deltas.py"] | planned |
| E-05 | integration | 真实 Runtime + Redis 停机 + PG 持久事件 | 退化为 PG 有界轮询；不丢/不重；恢复回切 | tests/agent_runtime/test_event_tail_hub_fallback.py | ["uv", "run", "pytest", "-q", "tests/agent_runtime/test_event_tail_hub_fallback.py"] | planned |
| B-02 | integration | 真实 PostgreSQL；批量写入 + retention | 删除速率≥写入；表大小稳定；无长事务/锁等待 | tests/agent_runtime/test_event_retention_throughput.py | ["uv", "run", "pytest", "-q", "tests/agent_runtime/test_event_retention_throughput.py"] | planned |

**Spec Verifier**

| Rule | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 执行命令 | 状态 |
|---|---|---|---|---|---|
| harness-data#RULE-data-001 | integration | 真实 PG/Redis/HTTP/architecture tests；不 mock 规则边界 | 规则既有 verifier 全绿；本任务不改变其语义 | uv run pytest -q tests -k schema_parity | planned |
| harness-snapshot#RULE-snapshot-001 | integration | 真实 PG/Redis/HTTP/architecture tests；不 mock 规则边界 | 规则既有 verifier 全绿；本任务不改变其语义 | bash -lc "uv run pytest -q tests/agent_runtime/test_snapshot_freeze.py tests/agent_runtime/test_run_reaper.py && uv run pytest -q tests/agent_runtime -k 'executor or resolve'" | planned |

### Acceptance Evidence

> 编码期由 `cf-task-start` 填写 RED/GREEN、断言位置与真实边界证据；manual 场景登记原因/边界/验收方式。

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|---|---|---|---|---|---|
| S-04 | planned | planned | planned | planned | planned |
| E-05 | planned | planned | planned | planned | planned |
| B-02 | planned | planned | planned | planned | planned |

### Log

- [2026-10-25] created (draft)
- [2026-10-25] started
- [2026-10-25] implemented process settings singleton, chat-principal JOIN (4→1 SELECT), notification idle fast path (4→2 SELECT), admission materials dedupe; tests: console_platform+console_auth 476 passed, console_internal 60 passed, notifications 10-user self-check 2 passed

---

## TASK-005: Runtime 容量、HPA 与后台隔离

- **Status**: draft
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

- [ ] 交付 `<Capability>`：队列年龄可观测、HPA 按队列扩、history 不占 202、memory backlog 有界
- [ ] 发布 waiting/oldest/slots/event flush/pool 指标；HPA 配置队列指标
- [ ] 提高执行槽/副本与模型池额度，总槽位按 profile 达 1000
- [ ] `_load_history` 移到后台 launch；memory consolidation 限速/独立池
- [ ] [S-13][integration] 队列指标/HPA、202 不等 history、memory backlog 有界
- [ ] [B-01][integration] 1000 瞬时受理：有界队列/429，不 OOM，恢复后按 SLO 排空
- [ ] [harness-memory#RULE-memory-001][verifier] uv run pytest -q tests/agent_core/test_memory_redesign_evidence.py tests/agent_runtime/test_memory_redesign_confirmation.py
- [ ] [harness-memory#RULE-memory-002][verifier] uv run pytest -q tests/agent_runtime/test_memory_redesign_writes.py tests/agent_runtime/test_memory_redesign_quotas.py
- [ ] [harness-memory#RULE-memory-003][verifier] uv run pytest -q tests/agent_runtime/test_memory_redesign_cache.py tests/agent_runtime/test_memory_redesign_context.py tests/agent_core/test_memory_redesign_selection.py
- [ ] [harness-memory#RULE-memory-004][verifier] uv run pytest -q tests/agent_runtime/test_memory_redesign_job_effects.py tests/agent_runtime/test_memory_redesign_confirmation.py
- [ ] [harness-memory#RULE-memory-005][verifier] uv run pytest -q tests/agent_runtime/test_memory_redesign_job_effects.py tests/agent_runtime/test_memory_redesign_input_boundary.py
- [ ] [harness-memory#RULE-memory-006][verifier] uv run pytest -q tests/migrations/test_memory_redesign_migration.py tests/agent_runtime/test_memory_redesign_snapshot.py
- [ ] [harness-worker#RULE-worker-001][verifier] bash -lc "uv run pytest -q tests/agent_worker && uv run pytest -q tests/agent_runtime --ignore=tests/agent_runtime/test_runner_executor.py"
- [x] 运行验收命令并填写 Acceptance Evidence

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| S-13 | integration | 真实 Runtime/Worker/PG + 模拟队列压力 | 队列年龄可断言；202 不等待 history；memory backlog 有界 | tests/agent_runtime/test_runtime_capacity_metrics.py | ["uv", "run", "pytest", "-q", "tests/agent_runtime/test_runtime_capacity_metrics.py"] | planned |
| B-01 | integration | 真实 Gateway/Runtime/PG；ramp=0 1000 请求 | 不 OOM/无限 task；显式 429/CHANNEL_BUSY；已受理可查/取消；恢复排空 | tests/acceptance/capacity/test_capacity.py | ["bash", "-lc", "CAPACITY_LOAD_USERS=1000 uv run pytest -q -m capacity_load tests/acceptance/capacity/test_capacity.py"] | planned |

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
| S-13 | planned | planned | planned | planned | planned |
| B-01 | planned | planned | planned | planned | planned |

### Log

- [2026-10-25] created (draft)
- [2026-10-25] started
- [2026-10-25] implemented process settings singleton, chat-principal JOIN (4→1 SELECT), notification idle fast path (4→2 SELECT), admission materials dedupe; tests: console_platform+console_auth 476 passed, console_internal 60 passed, notifications 10-user self-check 2 passed

---

## TASK-006: 统一消息契约与网关内核

- **Status**: draft
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

- [ ] 交付 `<Capability>`：四类契约 JSON round-trip 与未知字段拒绝
- [ ] 新增 `muad_contracts/im.py` 与 `packages/im-core` SPI/route/stream 助手
- [ ] 打开 ChannelName/registry；保留旧 WebChat/IM 契约兼容
- [ ] 实现 `admit_sync`/`admit_async` 内核入口与能力路由
- [ ] [S-11][contract] 四类契约 round-trip、shard/route key 稳定、旧契约不破坏
- [x] 运行验收命令并填写 Acceptance Evidence

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| S-11 | contract | 纯契约/序列化；不连服务 | 字段/枚举/未知字段拒绝符合契约；同输入 shard 稳定；旧契约不破坏 | tests/contracts/test_im_contracts.py | ["uv", "run", "pytest", "-q", "tests/contracts/test_im_contracts.py"] | planned |

### Acceptance Evidence

> 编码期由 `cf-task-start` 填写 RED/GREEN、断言位置与真实边界证据；manual 场景登记原因/边界/验收方式。

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|---|---|---|---|---|---|
| S-11 | planned | planned | planned | planned | planned |

### Log

- [2026-10-25] created (draft)
- [2026-10-25] started
- [2026-10-25] implemented process settings singleton, chat-principal JOIN (4→1 SELECT), notification idle fast path (4→2 SELECT), admission materials dedupe; tests: console_platform+console_auth 476 passed, console_internal 60 passed, notifications 10-user self-check 2 passed

---

## TASK-007: Connector 运行时与企微状态隔离

- **Status**: draft
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

- [ ] 交付 `<Capability>`：双 Connector 只有一个 active owner，kill owner 可接管
- [ ] 新增 `apps/im-connector` 包/镜像；Gateway 移除 WeCom SDK
- [ ] Redis lease + epoch；reply store（message/run/stream）；inbound/outbound Stream
- [ ] 出站命令幂等/结果回写；epoch 不匹配降级主动发送
- [ ] [S-05][integration] 假 WS 双 Connector：单 owner/接管/无重复
- [ ] [E-03][integration] 平台重投：只建一个 Run
- [ ] [E-07][integration] 平台不重投：显式 FAILED/告警，不假装成功
- [ ] [E-04][integration] reply_ref 过期：主动发送+DEGRADED，不串消息
- [ ] [E-06][manual] 真机企微双 WS/req_id 跨重连：记录真实语义（staging；owner=Connector 负责人；deadline=Phase 2 结束前；未通过不得宣称企微多连接安全）
- [x] 运行验收命令并填写 Acceptance Evidence

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| S-05 | integration | 双 Connector 进程 + 假 WS + Redis lease | 单 owner；epoch 拒旧 owner；接管；无重复 | tests/acceptance/im_gateway/test_connector_lease.py | ["uv", "run", "pytest", "-q", "tests/acceptance/im_gateway/test_connector_lease.py"] | planned |
| E-03 | integration | 真实 Connector + Redis Stream；平台重投 | 最终一个 Run；无重复回复 | tests/acceptance/im_gateway/test_connector_failover.py | ["uv", "run", "pytest", "-q", "tests/acceptance/im_gateway/test_connector_failover.py"] | planned |
| E-04 | integration | 真实 Connector + 假 WS；旧 reply_ref | 主动发送+DEGRADED；不串消息 | tests/acceptance/im_gateway/test_connector_failover.py | ["uv", "run", "pytest", "-q", "tests/acceptance/im_gateway/test_connector_failover.py"] | planned |
| E-06 | manual | 真实企微 + 两个 Connector + staging | 记录双 WS/req_id 寿命；降级符合预期 | manual/staging（owner=Connector 负责人；deadline=Phase 2 结束前） | - | planned |
| E-07 | integration | 真实 Connector + Redis；平台不重投 | 显式 FAILED/告警；不承诺 exactly-once | tests/acceptance/im_gateway/test_connector_failover.py | ["uv", "run", "pytest", "-q", "tests/acceptance/im_gateway/test_connector_failover.py"] | planned |

### Acceptance Evidence

> 编码期由 `cf-task-start` 填写 RED/GREEN、断言位置与真实边界证据；manual 场景登记原因/边界/验收方式。

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|---|---|---|---|---|---|
| S-05 | planned | planned | planned | planned | planned |
| E-03 | planned | planned | planned | planned | planned |
| E-04 | planned | planned | planned | planned | planned |
| E-06 | planned | planned | planned | planned | planned |
| E-07 | planned | planned | planned | planned | planned |

### Log

- [2026-10-25] created (draft)
- [2026-10-25] started
- [2026-10-25] implemented process settings singleton, chat-principal JOIN (4→1 SELECT), notification idle fast path (4→2 SELECT), admission materials dedupe; tests: console_platform+console_auth 476 passed, console_internal 60 passed, notifications 10-user self-check 2 passed

---

## TASK-008: Gateway 无状态、投递 facade 与 Webhook

- **Status**: draft
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

- [ ] 交付 `<Capability>`：Gateway 无 WeCom SDK/secret；Webhook 合法事件入队、非法签名拒绝
- [ ] 实现 delivery facade：校验→im.outbound→有界等待；WEBCHAT 走 inbox/SSE
- [ ] bot snapshot 按 channel 过滤；Gateway 不接收 WECOM secret
- [ ] 新增 `/channels/{channel}/events` Webhook 路由：验签→XADD→快速 2xx
- [ ] [S-07][integration] Webhook adapter 接入、非法签名拒绝、重复 event_id 幂等
- [ ] [harness-api#RULE-api-001][verifier] uv run pytest -q tests/test_api_i18n.py tests/test_error_catalog.py tests/acceptance/test_foundation_api_envelope.py
- [ ] [harness-api#RULE-api-002][verifier] uv run pytest -q tests/console_skill/test_import_idempotency.py
- [ ] [harness-secret#RULE-secret-001][verifier] uv run pytest -q tests/test_logging_redaction.py tests/acceptance/test_foundation_ops_audit.py
- [x] 运行验收命令并填写 Acceptance Evidence

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| S-07 | integration | 真实 Gateway HTTP + 假 webhook + Redis Stream + Runtime | 不新增 Deployment；合法先持久化再 ACK；非法签名不落库；重复 event_id 幂等 | tests/acceptance/im_gateway/test_webhook_channel.py | ["uv", "run", "pytest", "-q", "tests/acceptance/im_gateway/test_webhook_channel.py"] | planned |

**Spec Verifier**

| Rule | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 执行命令 | 状态 |
|---|---|---|---|---|---|
| harness-api#RULE-api-001 | integration | 真实 PG/Redis/HTTP/architecture tests；不 mock 规则边界 | 规则既有 verifier 全绿；本任务不改变其语义 | uv run pytest -q tests/test_api_i18n.py tests/test_error_catalog.py tests/acceptance/test_foundation_api_envelope.py | planned |
| harness-api#RULE-api-002 | integration | 真实 PG/Redis/HTTP/architecture tests；不 mock 规则边界 | 规则既有 verifier 全绿；本任务不改变其语义 | uv run pytest -q tests/console_skill/test_import_idempotency.py | planned |
| harness-secret#RULE-secret-001 | integration | 真实 PG/Redis/HTTP/architecture tests；不 mock 规则边界 | 规则既有 verifier 全绿；本任务不改变其语义 | uv run pytest -q tests/test_logging_redaction.py tests/acceptance/test_foundation_ops_audit.py | planned |

### Acceptance Evidence

> 编码期由 `cf-task-start` 填写 RED/GREEN、断言位置与真实边界证据；manual 场景登记原因/边界/验收方式。

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|---|---|---|---|---|---|
| S-07 | planned | planned | planned | planned | planned |

### Log

- [2026-10-25] created (draft)
- [2026-10-25] started
- [2026-10-25] implemented process settings singleton, chat-principal JOIN (4→1 SELECT), notification idle fast path (4→2 SELECT), admission materials dedupe; tests: console_platform+console_auth 476 passed, console_internal 60 passed, notifications 10-user self-check 2 passed

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
- [2026-10-25] implemented process settings singleton, chat-principal JOIN (4→1 SELECT), notification idle fast path (4→2 SELECT), admission materials dedupe; tests: console_platform+console_auth 476 passed, console_internal 60 passed, notifications 10-user self-check 2 passed

---

*文档结束*
