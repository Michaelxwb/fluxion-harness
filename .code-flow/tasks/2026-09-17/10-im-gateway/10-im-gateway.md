# Tasks: IM Gateway 与主动投递

- **Source**: .code-flow/tasks/2026-09-17/10-im-gateway/（全部 design：10-im-gateway.backend.design.md）
- **Created**: 2026-09-20
- **Updated**: 2026-09-21
- **Plan-State**: planned（用户已确认写入；各 TASK 保持 draft，功能与 E2E 验收尚未执行）

## Proposal

补齐已有 Python IM Gateway 的多 Bot 连接、身份/命令、Runtime SSE 桥接和后台主动投递，修正现有实现与设计的契约差异。复用 Console 的身份/授权、Runtime 的 Run/Snapshot/取消、Worker 的持久投递事实，使 Gateway 保持渠道适配层职责，并以真实 HTTP/WS/PostgreSQL/Redis 验收跨模块结果。

共 29 个原子任务：P0 27、P1 2；本地依赖深度 9 层。每项列出 1–3 个预计改动文件（含测试），目标 15–60 分钟；外部服务启动与验收等待另计。超过范围先拆任务并校验 Context，禁止编码时静默扩展。B-101..B-129 是本次计划补充场景，已同步到 v1.2 design §2.5.3；原 13 个 S/E 场景保持 ID 与层级。

## Design Alignment

2026-09-21 用户确认继续并将任务写入本需求目录。下列局部设计修订已同步到 `10-im-gateway.backend.design.md` v1.2；这是设计/计划承接，不代表实现或 verifier 已通过。

- **持久幂等**：Console bind 复用已存在的 `control.skill_import_idempotency`（Agent/MCP 已使用），固定 `endpoint=/internal/channel/bind`，保留 partial unique、指纹、首次成功响应及事务原子性，不新增表。Gateway 传原 message_id；同 key/同指纹 200 重放，异指纹按 required Rule 使用 409 `COMMON_CONFLICT`。Runtime 当前 `IDEMPOTENCY_MISMATCH` 是 Owner 实现差异，TASK-026 的外部验收依赖在 Runtime 对齐前不得视为完成；`/new` 的重复提交同样由 Runtime 持久幂等。
- **列表契约**：API-01 Bot 快照保留 revision，补 items/page/page_size/total；跨页 revision/total 一致才发布完整快照，不一致保留旧快照到下一节拍重拉。API-04 Skills 在授权过滤后分页；page>=1、1<=page_size<=100。Console/Gateway 同步实现，禁止借“小集合”豁免 required Rule。
- **投递恢复**：API-05 成功键 TTL 7d，与 owner token 短租约分离；处理中不得当作 deduplicated 成功，明确发送失败释放租约并重试，崩溃到期恢复。Redis 不可用或外部发送结果不确定只承诺 at-least-once。原子去重/失败恢复由 EXT-09-021 实现，本模块承担消费契约与最终验收。
- **Spec 与验收**：Matrix 使用 Context 实际 spec_id、8 个 required Rule 的原 verifier_ref；覆盖 13 个原始场景、29 个补充场景、10 条业务规则、3 个风险。每条 Spec Rule 唯一最终负责人见 Coverage / Contract，原 E2E 不降级；授权以 B-124 验证，Snapshot/CAS 以 B-125 验证。
- **就绪与密钥**：依据 manager 初始化、完整 snapshot 和事件循环判定 readiness；单 bot 故障在 detail 降级，不要求全部 CONNECTED。Secret 仅 Owner 表和 Console→Gateway 最小内部快照传输，禁止公开 API/日志/审计/Snapshot/Prompt/IM 回显，不引入 SecretProvider。

## Baseline and External Dependencies

- 当前 Gateway 是 Python/FastAPI，已有 SDK Adapter、snapshot、inbound、dedupe、delivery 和测试，故本计划按现有代码补缺口，不另建 TypeScript Gateway。
- 已核实差异：投递为 exists→send→mark，响应字段 duplicate，Redis 检查失败直接报错；RuntimeClient 未传 Idempotency-Key；SSE 只保存 type/data；/stop 一律返回受理；任一 secret 错误会停止全部 bot；inbound 消费长流会阻塞后续命令；Console 缺 /internal/channel/skills，bind 未接 Header 幂等。
- **EXT-02/07**：Console 已有身份、AgentAccessGrant、Binding/Effective Capability；复用其事务和公式。TASK-003/004 是本计划明确的 Console Owner 补充工作，Gateway 不直接写 control/task 表。
- **EXT-08**：08-runtime-execution 的生产 Run HTTP/SSE、自动 resume、cancel-active、幂等、Snapshot、Reaper 必须可用；当前 Runtime 任务已标记 verified，但本模块仍需核对对应真实链路证据与下述协议差异。TASK-011/012/025/026 依赖这些真实行为，不能拿直接更新 DB 的测试冒充 Gateway→Runtime E2E。
- **EXT-09-020/021/043**：09-task-schedule 的持久投递重试、Gateway 原子去重/失败恢复、Worker 端最终投递验收目前为 draft；TASK-017/027 的启动以相应外部任务完成并提供 Evidence 为前提。它们不计入本文件局部 Depends DAG；不得以 mock 代替，也不重复分配其实现代码。
- Task 文档命令从仓库根目录执行。表中测试/选择器均为 planned；既有测试文件也须补对应 ID 用例后才能执行验收。原 Spec verifier 命令保留原样，新增场景命令作为增强，不替代它。
- 真实边界：生产 Console/Gateway/Runtime/Worker 进程与 HTTP/SSE、PostgreSQL、Redis、官方 SDK、WS socket 不得 mock。企业微信服务端以本地真实 WS 协议探针承载，模型用现有真实 HTTP 探针；不声称企业微信生产账号实网已验证。单元测试可隔离纯逻辑，不能冒充 E2E。
- 所有新增/修复先补失败用例并记录 RED；已有正确行为先跑回归，不人为制造失败。清理 e2e-im-* DB 数据/Redis keys/进程。环境缺失、skip 或外部任务未完成不能记 verified。
- **外部依赖启动约束**：凡 External-Depends 未满足，启动该 TASK 前先核对 Owner 任务状态与对应验收证据；缺失时记录为 blocked，不把其他模块已 verified 当作协议差异已修复。Runtime 的 COMMON_CONFLICT 与 /new 幂等是明确目标，外部实现待对齐；不影响无该依赖任务的规划门禁。

## Task Overview

| TASK | 优先级 | 标题 | 依赖 | 来源章节 | 验收 | Checklist |
|---|---|---|---|---|---|---|
| TASK-001 | P0 | 收紧 Channel 与 Delivery 公共契约 | 无 | 3.3 数据设计；3.4 接口设计 | B-101(unit) | 3 |
| TASK-002 | P0 | 统一 Console 客户端封套解析与链路头 | 001, 021 | 3.4 接口设计；3.5 质量实现方案 | B-102(integration) | 3 |
| TASK-003 | P0 | 补 Console bind 持久幂等与事务重放 | 001, 021 | API-03 执行绑定；3.3 数据设计；Spec Compliance Matrix | B-103(integration) | 3 |
| TASK-004 | P0 | 补齐 Console Effective Skills 内部端点 | 001, 021 | API-04 查询可用 Skills；2.5.1 业务规则与约束 | B-104(integration) | 3 |
| TASK-005 | P0 | 补 Bot 快照轮询与热更新边界 | 001, 002, 021 | 3.2.2 Bot 快照轮询与 Secret 解析；API-01 Bot 列表 | B-105(integration) | 3 |
| TASK-006 | P0 | 修复多 Bot 故障隔离与 WS 退避 | 005, 021 | 3.2.1 WebSocket 连接状态机；3.2.2 Bot 快照轮询与 Secret 解析 | B-106(integration) | 3 |
| TASK-007 | P0 | 对齐启动、就绪与关闭语义 | 005, 006, 021 | 4.1 健康检查与启动校验；3.2.1 WebSocket 连接状态机 | B-107(integration) | 3 |
| TASK-008 | P0 | 验证入站 Redis 原子去重与降级 | 001, 021 | 3.2.3 入站去重 | B-108(integration), S-05(integration), RULE-08(integration) | 5 |
| TASK-009 | P0 | 对齐 resolve、未绑定与授权分流 | 001, 002, 021 | API-02 解析消息路由；API-06 Runtime Run 桥接 | B-109(integration), E-01(integration) | 4 |
| TASK-010 | P0 | 完善 /bind 命令与稳定幂等键 | 002, 003, 021 | 3.4.2 内置命令与文案映射（FEAT-02）；API-03 执行绑定 | B-110(integration) | 3 |
| TASK-011 | P0 | 对齐 /skills 与 /new 命令输出 | 002, 004, 009, 013, 021 | 3.4.2 内置命令与文案映射（FEAT-02）；API-04 查询可用 Skills | B-111(integration) | 3 |
| TASK-012 | P0 | 修复 /stop 已取消与取消中文案 | 009, 013, 021 | 3.4.2 内置命令与文案映射（FEAT-02） | B-112(integration), S-06(integration), E-05(integration) | 5 |
| TASK-013 | P0 | 补 Runtime 客户端幂等头与请求上下文 | 001, 021 | API-06 Runtime Run 桥接；3.4.2 内置命令与文案映射（FEAT-02） | B-113(integration) | 3 |
| TASK-014 | P0 | 使 SSE 解析保留封套与序号 | 013 | 3.4.1 Runtime SSE 事件处理（FEAT-03） | B-114(unit) | 3 |
| TASK-015 | P0 | 补齐 SSE 到 IM 的收尾与中断呈现 | 014, 021 | 3.4.1 Runtime SSE 事件处理（FEAT-03）；3.4.2 内置命令与文案映射（FEAT-02） | B-115(integration) | 3 |
| TASK-016 | P0 | 避免长流阻塞后续消息与 /stop | 009, 010, 011, 012, 015, 021 | 3.2 架构与流程；API-06 Runtime Run 桥接；3.4.1 Runtime SSE 事件处理（FEAT-03） | B-116(integration) | 3 |
| TASK-017 | P0 | 对齐主动投递响应与渠道错误 | 001, 006, 021 | API-05 主动投递 | B-117(integration) | 3 |
| TASK-018 | P1 | 补连接状态指标与脱敏日志 | 006, 007, 021 | 4.2 指标目录；3.2.1 WebSocket 连接状态机；3.5 质量实现方案 | B-118(integration) | 3 |
| TASK-019 | P1 | 补消息、去重与投递指标 | 008, 015, 017, 018, 021 | 4.2 指标目录；3.5 质量实现方案 | B-119(integration) | 3 |
| TASK-020 | P0 | 建立真实 WS 与官方 SDK 测试边界 | 无 | 2.5.2 功能验收场景；3.1 技术选型与关键决策 | B-120(integration) | 3 |
| TASK-021 | P0 | 建立 Gateway 多服务真实验收环境 | 020 | 2.5.2 功能验收场景；3.5 质量实现方案 | B-121(integration) | 3 |
| TASK-022 | P0 | 验收多 Bot 路由与任意 Runtime 实例 | 005, 006, 009, 013, 016, 021 | 2.5.2 功能验收场景；3.1 技术选型与关键决策；Spec Compliance Matrix | B-122(E2E), S-01(E2E), RULE-01(E2E), RULE-04(E2E), RISK-01(E2E), RULE-arch-001(E2E), RULE-im-001(E2E) | 9 |
| TASK-023 | P0 | 验收绑定链路与双语 API 封套 | 002, 003, 010, 021 | 2.5.2 功能验收场景；API-03 执行绑定；3.4 接口设计；Spec Compliance Matrix | B-123(E2E), S-02(E2E), E-02(integration), RULE-02(E2E), RULE-api-001(E2E) | 7 |
| TASK-024 | P0 | 验收 Effective Capability 与命令权限 | 004, 009, 011, 021 | API-04 查询可用 Skills；API-02 解析消息路由；2.5.1 业务规则与约束；Spec Compliance Matrix | B-124(E2E), RULE-05(E2E), RULE-auth-001(E2E) | 5 |
| TASK-025 | P0 | 验收流式回复、Resume、取消与 Snapshot | 012, 014, 015, 016, 021 | 2.5.2 功能验收场景；3.4.1 Runtime SSE 事件处理（FEAT-03）；Spec Compliance Matrix | B-125(E2E), S-03(E2E), E-03(integration), E-04(integration), RULE-06(E2E), RULE-10(E2E), RULE-snapshot-001(E2E) | 9 |
| TASK-026 | P0 | 验收绑定和 Run 的端到端幂等 | 003, 010, 013, 021 | API-03 执行绑定；API-06 Runtime Run 桥接；Spec Compliance Matrix | B-126(E2E), RULE-api-002(E2E) | 4 |
| TASK-027 | P0 | 验收 Worker 主动投递及 Redis 故障 | 008, 017, 021 | 2.5.2 功能验收场景；API-05 主动投递；3.2.3 入站去重；5. 风险与依赖 | B-127(E2E), S-04(E2E), E-06(integration), RULE-09(E2E), RISK-02(E2E) | 7 |
| TASK-028 | P0 | 验收 Secret 不泄露与单 Bot 隔离 | 006, 007, 017, 018, 021 | 2.5.2 功能验收场景；3.2.2 Bot 快照轮询与 Secret 解析；3.5 质量实现方案；Spec Compliance Matrix | B-128(integration), E-07(integration), RULE-03(integration), RISK-03(integration), RULE-secret-001(E2E) | 7 |
| TASK-029 | P0 | 收口全部场景、规则与验收证据 | 001, 002, 003, 004, 005, 006, 007, 008, 009, 010, 011, 012, 013, 014, 015, 016, 017, 018, 019, 020, 021, 022, 023, 024, 025, 026, 027, 028 | 2.5.2 功能验收场景；5. 风险与依赖；6. 需求追溯矩阵；Spec Compliance Matrix | B-129(integration), RULE-07(E2E), RULE-test-001(E2E) | 5 |

## Acceptance Coverage

每个场景、业务 RULE、RISK 与 required Spec Rule 均只在一个 TASK 中作为最终负责人。跨任务实现依赖见 Depends；其他 TASK 可复核行为，但不重复登记最终 owner。

| 场景ID | 来源设计 | 测试层级 | 关键真实边界 | 负责任务 | 状态 | 执行命令 argv | cwd | timeout | depends_on |
|---|---|---|---|---|---|---|---|---|---|
| B-101 | 10-im-gateway.backend.design.md#3.3 数据设计 | unit | 真实 Pydantic DTO 校验与 JSON 序列化 | TASK-001 | planned | ["uv","run","pytest","-q","tests/gateway/test_channel_contracts.py","-k","b101"] | . | 600 |  |
| B-102 | 10-im-gateway.backend.design.md#3.4 接口设计 | integration | 生产 ConsoleClient→真实本地 HTTP 服务→Envelope 解码 | TASK-002 | planned | ["uv","run","pytest","-q","tests/gateway/test_gateway_console_client.py","-k","b102"] | . | 600 |  |
| B-103 | 10-im-gateway.backend.design.md#API-03 执行绑定 | integration | 真实 bind HTTP handler→PostgreSQL 幂等记录、bind_code 行锁、channel_identity | TASK-003 | planned | ["uv","run","pytest","-q","tests/console_channel/test_channel_bind_idempotency.py","-k","b103"] | . | 600 |  |
| B-104 | 10-im-gateway.backend.design.md#API-04 查询可用 Skills | integration | 真实 Console handler→生产授权服务→PostgreSQL Agent/Skill/Grant | TASK-004 | planned | ["uv","run","pytest","-q","tests/console_channel/test_channel_skills_api.py","-k","b104"] | . | 600 |  |
| B-105 | 10-im-gateway.backend.design.md#3.2.2 Bot 快照轮询与 Secret 解析 | integration | Console snapshot HTTP→真实 PG bot 配置→BotSnapshotCache | TASK-005 | planned | ["uv","run","pytest","-q","tests/gateway/test_bot_snapshot.py","-k","b105"] | . | 600 |  |
| B-106 | 10-im-gateway.backend.design.md#3.2.1 WebSocket 连接状态机 | integration | 生产 WeComAdapter/连接管理器→真实本地 WS 故障探针 | TASK-006 | planned | ["uv","run","pytest","-q","tests/gateway/test_wecom_adapter.py","-k","b106"] | . | 600 |  |
| B-107 | 10-im-gateway.backend.design.md#4.1 健康检查与启动校验 | integration | 真实 Gateway lifespan/HTTP probes→Console/WS 连接管理器 | TASK-007 | planned | ["uv","run","pytest","-q","tests/gateway/test_readyz.py","-k","b107"] | . | 600 |  |
| B-108 | 10-im-gateway.backend.design.md#3.2.3 入站去重 | integration | Gateway inbound→真实 Redis→真实 Runtime HTTP 接收边界 | TASK-008 | planned | ["uv","run","pytest","-q","tests/gateway/test_inbound_dedupe_integration.py","-k","b108"] | . | 600 |  |
| S-05 | 10-im-gateway.backend.design.md#2.5.2 功能验收场景 | integration | Gateway入站→真实Redis dedupe→真实下游HTTP观测 | TASK-008 | planned | ["uv","run","pytest","-q","tests/gateway/test_inbound_dedupe_integration.py","-k","s05"] | . | 600 |  |
| RULE-08 | 10-im-gateway.backend.design.md#2.5.1 业务规则与约束 | integration | Gateway inbound→真实 Redis→真实 Runtime HTTP 接收边界 | TASK-008 | planned | ["uv","run","pytest","-q","tests/gateway/test_inbound_dedupe_integration.py","-k","b108"] | . | 600 |  |
| B-109 | 10-im-gateway.backend.design.md#API-02 解析消息路由 | integration | Gateway→真实 Console resolve HTTP→PostgreSQL→Runtime 接收观测 | TASK-009 | planned | ["uv","run","pytest","-q","tests/gateway/test_message_routing_integration.py","-k","b109"] | . | 600 |  |
| E-01 | 10-im-gateway.backend.design.md#2.5.2 功能验收场景 | integration | Gateway→真实bot resolve HTTP→PostgreSQL | TASK-009 | planned | ["uv","run","pytest","-q","tests/gateway/test_message_routing_integration.py","-k","e01"] | . | 600 |  |
| B-110 | 10-im-gateway.backend.design.md#3.4.2 内置命令与文案映射（FEAT-02） | integration | Gateway command→真实 Console bind HTTP→PostgreSQL | TASK-010 | planned | ["uv","run","pytest","-q","tests/gateway/test_bind_command.py","-k","b110"] | . | 600 |  |
| B-111 | 10-im-gateway.backend.design.md#3.4.2 内置命令与文案映射（FEAT-02） | integration | Gateway commands→真实 Console/Runtime HTTP→PostgreSQL | TASK-011 | planned | ["uv","run","pytest","-q","tests/gateway/test_commands_integration.py","-k","b111"] | . | 600 |  |
| B-112 | 10-im-gateway.backend.design.md#3.4.2 内置命令与文案映射（FEAT-02） | integration | Gateway→真实 Runtime cancel-active HTTP→PostgreSQL CAS/事件 | TASK-012 | planned | ["uv","run","pytest","-q","tests/gateway/test_stop_integration.py","-k","b112"] | . | 600 |  |
| S-06 | 10-im-gateway.backend.design.md#2.5.2 功能验收场景 | integration | 真实Gateway→Runtime cancel-active HTTP→PostgreSQL CAS | TASK-012 | planned | ["uv","run","pytest","-q","tests/gateway/test_stop_integration.py","-k","s06"] | . | 600 |  |
| E-05 | 10-im-gateway.backend.design.md#2.5.2 功能验收场景 | integration | 真实Runtime cancel-active/PG→Gateway | TASK-012 | planned | ["uv","run","pytest","-q","tests/gateway/test_stop_integration.py","-k","e05"] | . | 600 |  |
| B-113 | 10-im-gateway.backend.design.md#API-06 Runtime Run 桥接 | integration | 生产 RuntimeClient→真实本地 HTTP/SSE 接收端 | TASK-013 | planned | ["uv","run","pytest","-q","tests/gateway/test_runtime_client.py","-k","b113"] | . | 600 |  |
| B-114 | 10-im-gateway.backend.design.md#3.4.1 Runtime SSE 事件处理（FEAT-03） | unit | 真实 SSE parser 与分片字节/行输入 | TASK-014 | planned | ["uv","run","pytest","-q","tests/gateway/test_sse_parser.py","-k","b114"] | . | 600 |  |
| B-115 | 10-im-gateway.backend.design.md#3.4.1 Runtime SSE 事件处理（FEAT-03） | integration | 真实 SSE 解析→生产 renderer→真实本地 WS SDK 出站 | TASK-015 | planned | ["uv","run","pytest","-q","tests/gateway/test_stream_renderer.py","-k","b115"] | . | 600 |  |
| B-116 | 10-im-gateway.backend.design.md#3.2 架构与流程 | integration | 真实 iter_events→Gateway 消费队列→Runtime HTTP/SSE→WS 回复 | TASK-016 | planned | ["uv","run","pytest","-q","tests/gateway/test_inbound_concurrency.py","-k","b116"] | . | 600 |  |
| B-117 | 10-im-gateway.backend.design.md#API-05 主动投递 | integration | 真实 Gateway HTTP→生产 Adapter→真实 Redis/本地 WS | TASK-017 | planned | ["uv","run","pytest","-q","tests/gateway/test_delivery_api.py","-k","b117"] | . | 600 |  |
| B-118 | 10-im-gateway.backend.design.md#4.2 指标目录 | integration | 真实连接迁移→生产日志/metrics exporter | TASK-018 | planned | ["uv","run","pytest","-q","tests/gateway/test_connection_observability.py","-k","b118"] | . | 600 |  |
| B-119 | 10-im-gateway.backend.design.md#4.2 指标目录 | integration | 生产入站/HTTP投递/真实SSE→metrics exporter | TASK-019 | planned | ["uv","run","pytest","-q","tests/gateway/test_message_metrics.py","-k","b119"] | . | 600 |  |
| B-120 | 10-im-gateway.backend.design.md#2.5.2 功能验收场景 | integration | 官方 SDK→真实本地 WebSocket 服务→生产 WeComAdapter | TASK-020 | planned | ["uv","run","pytest","-q","tests/acceptance/im_gateway/test_wecom_boundary.py","-k","b120"] | . | 600 |  |
| B-121 | 10-im-gateway.backend.design.md#2.5.2 功能验收场景 | integration | 生产进程生命周期→真实HTTP/WS/PostgreSQL/Redis | TASK-021 | planned | ["uv","run","pytest","-q","tests/acceptance/im_gateway/test_environment.py","-k","b121"] | . | 600 |  |
| B-122 | 10-im-gateway.backend.design.md#2.5.2 功能验收场景 | E2E | 官方SDK/WeComAdapter→Gateway→Console/PG→真实双Runtime HTTP | TASK-022 | planned | ["uv","run","pytest","-q","tests/acceptance/im_gateway/test_routing.py","-k","b122"] | . | 1200 |  |
| S-01 | 10-im-gateway.backend.design.md#2.5.2 功能验收场景 | E2E | 官方SDK/WeComAdapter→Gateway→真实Console/PG与双Runtime | TASK-022 | planned | ["uv","run","pytest","-q","tests/acceptance/im_gateway/test_routing.py","-k","s01"] | . | 1200 |  |
| RULE-01 | 10-im-gateway.backend.design.md#2.5.1 业务规则与约束 | E2E | 官方SDK/WeComAdapter→Gateway→Console/PG→真实双Runtime HTTP | TASK-022 | planned | ["uv","run","pytest","-q","tests/acceptance/im_gateway/test_routing.py","-k","b122"] | . | 1200 |  |
| RULE-04 | 10-im-gateway.backend.design.md#2.5.1 业务规则与约束 | E2E | 官方SDK/WeComAdapter→Gateway→Console/PG→真实双Runtime HTTP | TASK-022 | planned | ["uv","run","pytest","-q","tests/acceptance/im_gateway/test_routing.py","-k","b122"] | . | 1200 |  |
| RISK-01 | 10-im-gateway.backend.design.md#5. 风险与依赖 | E2E | 官方SDK/WeComAdapter→Gateway→Console/PG→真实双Runtime HTTP | TASK-022 | planned | ["uv","run","pytest","-q","tests/acceptance/im_gateway/test_routing.py","-k","b122"] | . | 1200 |  |
| RULE-arch-001 | 10-im-gateway.backend.design.md#Spec Compliance Matrix | E2E | 官方SDK/WeComAdapter→Gateway→Console/PG→真实双Runtime HTTP；原 Spec verifier 真实边界 | TASK-022 | planned | ["bash","-lc","uv run pytest -q tests/acceptance/im_gateway/test_routing.py && uv run pytest -q tests/architecture"] | . | 1200 |  |
| RULE-im-001 | 10-im-gateway.backend.design.md#Spec Compliance Matrix | E2E | 官方SDK/WeComAdapter→Gateway→Console/PG→真实双Runtime HTTP；原 Spec verifier 真实边界 | TASK-022 | planned | ["bash","-lc","uv run pytest -q tests/acceptance/im_gateway/test_routing.py && uv run pytest -q tests/console_channel tests/gateway"] | . | 1200 |  |
| B-123 | 10-im-gateway.backend.design.md#2.5.2 功能验收场景 | E2E | 真实WS→Gateway命令→Console bind HTTP→PostgreSQL→SDK回复 | TASK-023 | planned | ["uv","run","pytest","-q","tests/acceptance/im_gateway/test_binding.py","-k","b123"] | . | 1200 |  |
| S-02 | 10-im-gateway.backend.design.md#2.5.2 功能验收场景 | E2E | Gateway完整/bind命令→真实Console HTTP→PostgreSQL→最终回复 | TASK-023 | planned | ["uv","run","pytest","-q","tests/acceptance/im_gateway/test_binding.py","-k","s02"] | . | 1200 |  |
| E-02 | 10-im-gateway.backend.design.md#2.5.2 功能验收场景 | integration | 真实Console bind HTTP→PostgreSQL bind_code/identity | TASK-023 | planned | ["uv","run","pytest","-q","tests/acceptance/im_gateway/test_binding.py","-k","e02"] | . | 600 |  |
| RULE-02 | 10-im-gateway.backend.design.md#2.5.1 业务规则与约束 | E2E | 真实WS→Gateway命令→Console bind HTTP→PostgreSQL→SDK回复 | TASK-023 | planned | ["uv","run","pytest","-q","tests/acceptance/im_gateway/test_binding.py","-k","b123"] | . | 1200 |  |
| RULE-api-001 | 10-im-gateway.backend.design.md#Spec Compliance Matrix | E2E | 真实WS→Gateway命令→Console bind HTTP→PostgreSQL→SDK回复；原 Spec verifier 真实边界 | TASK-023 | planned | ["bash","-lc","uv run pytest -q tests/acceptance/im_gateway/test_binding.py && uv run pytest -q tests/test_api_i18n.py tests/test_error_catalog.py tests/acceptance/test_foundation_api_envelope.py"] | . | 1200 |  |
| B-124 | 10-im-gateway.backend.design.md#API-04 查询可用 Skills | E2E | WS命令→Gateway→Console授权HTTP/PG→Runtime Prompt/ToolRegistry | TASK-024 | planned | ["uv","run","pytest","-q","tests/acceptance/im_gateway/test_authorization.py","-k","b124"] | . | 1200 |  |
| RULE-05 | 10-im-gateway.backend.design.md#2.5.1 业务规则与约束 | E2E | WS命令→Gateway→Console授权HTTP/PG→Runtime Prompt/ToolRegistry | TASK-024 | planned | ["uv","run","pytest","-q","tests/acceptance/im_gateway/test_authorization.py","-k","b124"] | . | 1200 |  |
| RULE-auth-001 | 10-im-gateway.backend.design.md#Spec Compliance Matrix | E2E | WS命令→Gateway→Console授权HTTP/PG→Runtime Prompt/ToolRegistry；原 Spec verifier 真实边界 | TASK-024 | planned | ["bash","-lc","uv run pytest -q tests/acceptance/im_gateway/test_authorization.py && uv run pytest -q tests/console_platform/test_user_side_relations.py -k s04 && uv run pytest -q tests -k schema_parity"] | . | 1200 |  |
| B-125 | 10-im-gateway.backend.design.md#2.5.2 功能验收场景 | E2E | 真实WS→Gateway→Runtime HTTP/SSE→PostgreSQL Snapshot/Reaper→SDK回复 | TASK-025 | planned | ["uv","run","pytest","-q","tests/acceptance/im_gateway/test_runtime_stream.py","-k","b125"] | . | 1200 |  |
| S-03 | 10-im-gateway.backend.design.md#2.5.2 功能验收场景 | E2E | 真实WeCom协议WS→Gateway→Runtime SSE/PG→官方SDK出站 | TASK-025 | planned | ["uv","run","pytest","-q","tests/acceptance/im_gateway/test_runtime_stream.py","-k","s03"] | . | 1200 |  |
| E-03 | 10-im-gateway.backend.design.md#2.5.2 功能验收场景 | integration | Gateway→真实Runtime SSE断线→Reaper/PostgreSQL | TASK-025 | planned | ["uv","run","pytest","-q","tests/acceptance/im_gateway/test_recovery.py","-k","e03"] | . | 600 |  |
| E-04 | 10-im-gateway.backend.design.md#2.5.2 功能验收场景 | integration | 真实Runtime/PG→Gateway HTTP错误处理 | TASK-025 | planned | ["uv","run","pytest","-q","tests/acceptance/im_gateway/test_runtime_stream.py","-k","e04"] | . | 600 |  |
| RULE-06 | 10-im-gateway.backend.design.md#2.5.1 业务规则与约束 | E2E | 真实WS→Gateway→Runtime HTTP/SSE→PostgreSQL Snapshot/Reaper→SDK回复 | TASK-025 | planned | ["uv","run","pytest","-q","tests/acceptance/im_gateway/test_runtime_stream.py","-k","b125"] | . | 1200 |  |
| RULE-10 | 10-im-gateway.backend.design.md#2.5.1 业务规则与约束 | E2E | 真实WS→Gateway→Runtime HTTP/SSE→PostgreSQL Snapshot/Reaper→SDK回复 | TASK-025 | planned | ["uv","run","pytest","-q","tests/acceptance/im_gateway/test_runtime_stream.py","-k","b125"] | . | 1200 |  |
| RULE-snapshot-001 | 10-im-gateway.backend.design.md#Spec Compliance Matrix | E2E | 真实WS→Gateway→Runtime HTTP/SSE→PostgreSQL Snapshot/Reaper→SDK回复；原 Spec verifier 真实边界 | TASK-025 | planned | ["bash","-lc","uv run pytest -q tests/acceptance/im_gateway/test_runtime_stream.py && uv run pytest -q tests/agent_runtime -k \"executor or resolve\""] | . | 1200 |  |
| B-126 | 10-im-gateway.backend.design.md#API-03 执行绑定 | E2E | Gateway HTTP→Console/Runtime→真实PostgreSQL幂等表/partial unique→可观测副作用 | TASK-026 | planned | ["uv","run","pytest","-q","tests/acceptance/im_gateway/test_idempotency.py","-k","b126"] | . | 1200 |  |
| RULE-api-002 | 10-im-gateway.backend.design.md#Spec Compliance Matrix | E2E | Gateway HTTP→Console/Runtime→真实PostgreSQL幂等表/partial unique→可观测副作用；原 Spec verifier 真实边界 | TASK-026 | planned | ["bash","-lc","uv run pytest -q tests/acceptance/im_gateway/test_idempotency.py && uv run pytest -q tests/console_skill/test_import_idempotency.py"] | . | 1200 |  |
| B-127 | 10-im-gateway.backend.design.md#2.5.2 功能验收场景 | E2E | 真实Worker/PG→Gateway HTTP→真实Redis→官方SDK/WS接收端 | TASK-027 | planned | ["uv","run","pytest","-q","tests/acceptance/im_gateway/test_delivery.py","-k","b127"] | . | 1200 |  |
| S-04 | 10-im-gateway.backend.design.md#2.5.2 功能验收场景 | E2E | 真实Worker→Gateway HTTP→Redis→官方SDK/真实WS接收 | TASK-027 | planned | ["uv","run","pytest","-q","tests/acceptance/im_gateway/test_delivery.py","-k","s04"] | . | 1200 |  |
| E-06 | 10-im-gateway.backend.design.md#2.5.2 功能验收场景 | integration | Gateway入站/投递→真实Redis连接故障→Runtime/WS | TASK-027 | planned | ["uv","run","pytest","-q","tests/acceptance/im_gateway/test_redis_degradation.py","-k","e06"] | . | 600 |  |
| RULE-09 | 10-im-gateway.backend.design.md#2.5.1 业务规则与约束 | E2E | 真实Worker/PG→Gateway HTTP→真实Redis→官方SDK/WS接收端 | TASK-027 | planned | ["uv","run","pytest","-q","tests/acceptance/im_gateway/test_delivery.py","-k","b127"] | . | 1200 |  |
| RISK-02 | 10-im-gateway.backend.design.md#5. 风险与依赖 | E2E | 真实Worker/PG→Gateway HTTP→真实Redis→官方SDK/WS接收端 | TASK-027 | planned | ["uv","run","pytest","-q","tests/acceptance/im_gateway/test_delivery.py","-k","b127"] | . | 1200 |  |
| B-128 | 10-im-gateway.backend.design.md#2.5.2 功能验收场景 | integration | 真实PG bot secret→Console内部快照HTTP→Gateway/SDK→日志/审计/快照输出 | TASK-028 | planned | ["uv","run","pytest","-q","tests/acceptance/im_gateway/test_secrets_and_readiness.py","-k","b128"] | . | 600 |  |
| E-07 | 10-im-gateway.backend.design.md#2.5.2 功能验收场景 | integration | 真实PG bot secret→Console快照→多bot SDK连接/readyz | TASK-028 | planned | ["uv","run","pytest","-q","tests/acceptance/im_gateway/test_secrets_and_readiness.py","-k","e07"] | . | 600 |  |
| RULE-03 | 10-im-gateway.backend.design.md#2.5.1 业务规则与约束 | integration | 真实PG bot secret→Console内部快照HTTP→Gateway/SDK→日志/审计/快照输出 | TASK-028 | planned | ["uv","run","pytest","-q","tests/acceptance/im_gateway/test_secrets_and_readiness.py","-k","b128"] | . | 600 |  |
| RISK-03 | 10-im-gateway.backend.design.md#5. 风险与依赖 | integration | 真实PG bot secret→Console内部快照HTTP→Gateway/SDK→日志/审计/快照输出 | TASK-028 | planned | ["uv","run","pytest","-q","tests/acceptance/im_gateway/test_secrets_and_readiness.py","-k","b128"] | . | 600 |  |
| RULE-secret-001 | 10-im-gateway.backend.design.md#Spec Compliance Matrix | E2E | 真实Worker投递/PG与Console快照→Gateway→官方SDK/WS；日志/审计/Snapshot/Prompt/对外响应 | TASK-028 | planned | ["bash","-lc","uv run pytest -q tests/acceptance/im_gateway/test_secrets_and_readiness.py && uv run pytest -q tests/test_logging_redaction.py tests/acceptance/test_foundation_ops_audit.py"] | . | 1200 |  |
| B-129 | 10-im-gateway.backend.design.md#2.5.2 功能验收场景 | integration | pytest用例收集/运行→验收Contract/Evidence→真实组件记录 | TASK-029 | planned | ["uv","run","pytest","-q","tests/acceptance/im_gateway/test_acceptance_inventory.py","-k","b129"] | . | 600 |  |
| RULE-07 | 10-im-gateway.backend.design.md#2.5.1 业务规则与约束 | E2E | 生产HTTP/SSE、PostgreSQL、Redis、官方SDK/WS与原verifier Chrome | TASK-029 | planned | ["bash","-lc","uv run pytest -q tests/acceptance && npm --prefix apps/console-platform/frontend run build && npm --prefix e2e test"] | . | 1200 |  |
| RULE-test-001 | 10-im-gateway.backend.design.md#Spec Compliance Matrix | E2E | 真实生产HTTP/SSE、PostgreSQL、Redis、官方SDK/WS与原verifier Chrome→验收Evidence | TASK-029 | planned | ["bash","-lc","uv run pytest -q tests/acceptance && npm --prefix apps/console-platform/frontend run build && npm --prefix e2e test"] | . | 1200 |  |

## Rule and Risk Traceability

| 设计约束 / 风险 | 关联场景 | 最终负责人 |
|---|---|---|
| RULE-01：四部署单元、Runtime/Worker无状态与任意实例 | S-01 | TASK-022 |
| RULE-02：统一封套、catalog错误码及分页 | S-02, E-02 | TASK-023 |
| RULE-03：Owner表密钥存储与禁止输出 | S-01, E-07 | TASK-028 |
| RULE-04：Agent 0..N bot，bot只路由一个Agent，无Pod绑定 | S-01, E-01 | TASK-022 |
| RULE-05：三层授权与Effective Capability；补充B-124避免仅RUN_BUSY冒充授权验证 | S-03, E-04, B-124 | TASK-024 |
| RULE-06：Snapshot冻结与终态CAS | S-03, E-03 | TASK-025 |
| RULE-07：跨服务真实E2E证据；E-03保留原层级另有B-125 E2E增强 | S-01, S-03, E-03 | TASK-029 |
| RULE-08：入站Redis SET NX EX600、降级at-least-once | S-05, E-06 | TASK-008 |
| RULE-09：投递key、7d去重、200重放与降级 | S-04, E-06 | TASK-027 |
| RULE-10：未绑定正常分支、自动resume、并发和取消错误语义 | S-06, E-04, E-05, B-125 | TASK-025 |
| RISK-01：SDK类型隔离，iter_events唯一入口 | S-01 | TASK-022 |
| RISK-02：Redis不可用的入站/投递语义 | E-06 | TASK-027 |
| RISK-03：WS抖动、secret失效、单bot隔离 | E-07 | TASK-028 |

---

## TASK-001: 收紧 Channel 与 Delivery 公共契约

- **Status**: draft
- **Priority**: P0
- **Depends**: 
- **Source**: 10-im-gateway.backend.design.md#3.3 数据设计, 10-im-gateway.backend.design.md#3.4 接口设计
- **Spec-Refs**: 
- **Acceptance-Refs**: B-101
- **Files**: `packages/contracts/src/muad_contracts/channel.py`, `packages/contracts/src/muad_contracts/delivery.py`, `tests/gateway/test_channel_contracts.py`
- **Estimate**: 15–60 分钟；超出先拆分

### Description

补齐 resolve 的 external_conversation_id、强类型 Skill/Delivery 响应；限定 message.type=text，验证 delivery_key 中 task_id 与请求一致、未知 channel 安全拒绝。Bot 快照/Skills 分页字段按 v1.2 设计采用统一 Page 语义，快照保留 revision。

### Checklist

- [ ] [B-101][unit] 修改生产代码前先按 真实 Pydantic DTO 校验与 JSON 序列化 编写或扩展用例并记录 RED；关键断言：合法/缺字段/非法 UUID/未知枚举；deduplicated 字段；Secret 不因 repr/错误详情外泄；页码边界。执行 argv：`["uv","run","pytest","-q","tests/gateway/test_channel_contracts.py","-k","b101"]`。
- [ ] 实现或补齐：补齐 resolve 的 external_conversation_id、强类型 Skill/Delivery 响应；限定 message.type=text，验证 delivery_key 中 task_id 与请求一致、未知 channel 安全拒绝。Bot 快照/Skills 分页字段按 v1.2 设计采用统一 Page 语义，快照保留 revision。
- [ ] 执行上述契约命令，填写 Acceptance Evidence 的 RED/GREEN、每个关键断言位置、真实组件与清理记录；失败、skip 或外部阻塞保留未验证。所有代码改动有对应测试，函数≤50行，强类型与显式异常处理。

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| B-101 | unit | 真实 Pydantic DTO 校验与 JSON 序列化 | 合法/缺字段/非法 UUID/未知枚举；deduplicated 字段；Secret 不因 repr/错误详情外泄；页码边界 | tests/gateway/test_channel_contracts.py / B-101（planned） | `["uv","run","pytest","-q","tests/gateway/test_channel_contracts.py","-k","b101"]` | planned |

### Acceptance Evidence
> planned。编码期填 RED/GREEN 命令与结果、断言路径/用例/位置、真实组件证据、外部依赖状态与清理证据；全部 verified 才可 done。本次结构检查不代表功能测试通过。

### Log
- [2026-09-20] prepared (draft)
- [2026-09-21] 用户确认后写入；设计修订已承接，状态保持 draft。

---

## TASK-002: 统一 Console 客户端封套解析与链路头

- **Status**: draft
- **Priority**: P0
- **Depends**: TASK-001, TASK-021
- **Source**: 10-im-gateway.backend.design.md#3.4 接口设计, 10-im-gateway.backend.design.md#3.5 质量实现方案
- **Spec-Refs**: 
- **Acceptance-Refs**: B-102
- **Files**: `apps/im-gateway/src/muad_im_gateway/application/console_client.py`, `apps/im-gateway/src/muad_im_gateway/application/envelope.py`, `tests/gateway/test_gateway_console_client.py`
- **Estimate**: 15–60 分钟；超出先拆分

### Description

使用强类型 data 解析与 API 契约；透传 tenant/trace/request，依赖失败显式映射，移除 /skills 404 静默返回空目录；不用宽泛 Any 掩盖契约错误。

### Checklist

- [ ] [B-102][integration] 修改生产代码前先按 生产 ConsoleClient→真实本地 HTTP 服务→Envelope 解码 编写或扩展用例并记录 RED；关键断言：headers 保持；有效空目录区别于 404/坏 JSON/超时；错误 code 保留；内部 URL/响应体不回显。执行 argv：`["uv","run","pytest","-q","tests/gateway/test_gateway_console_client.py","-k","b102"]`。
- [ ] 实现或补齐：使用强类型 data 解析与 API 契约；透传 tenant/trace/request，依赖失败显式映射，移除 /skills 404 静默返回空目录；不用宽泛 Any 掩盖契约错误。
- [ ] 执行上述契约命令，填写 Acceptance Evidence 的 RED/GREEN、每个关键断言位置、真实组件与清理记录；失败、skip 或外部阻塞保留未验证。所有代码改动有对应测试，函数≤50行，强类型与显式异常处理。

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| B-102 | integration | 生产 ConsoleClient→真实本地 HTTP 服务→Envelope 解码 | headers 保持；有效空目录区别于 404/坏 JSON/超时；错误 code 保留；内部 URL/响应体不回显 | tests/gateway/test_gateway_console_client.py / B-102（planned） | `["uv","run","pytest","-q","tests/gateway/test_gateway_console_client.py","-k","b102"]` | planned |

### Acceptance Evidence
> planned。编码期填 RED/GREEN 命令与结果、断言路径/用例/位置、真实组件证据、外部依赖状态与清理证据；全部 verified 才可 done。本次结构检查不代表功能测试通过。

### Log
- [2026-09-20] prepared (draft)
- [2026-09-21] 用户确认后写入；设计修订已承接，状态保持 draft。

---

## TASK-003: 补 Console bind 持久幂等与事务重放

- **Status**: draft
- **Priority**: P0
- **Depends**: TASK-001, TASK-021
- **Source**: 10-im-gateway.backend.design.md#API-03 执行绑定, 10-im-gateway.backend.design.md#3.3 数据设计, 10-im-gateway.backend.design.md#Spec Compliance Matrix
- **Spec-Refs**: 
- **Acceptance-Refs**: B-103
- **Files**: `apps/console-platform/backend/src/muad_console_platform/api/internal_channel.py`, `apps/console-platform/backend/src/muad_console_platform/application/channel_service.py`, `tests/console_channel/test_channel_bind_idempotency.py`
- **Estimate**: 15–60 分钟；超出先拆分

### Description

在 Console Owner 事务内复用现有幂等存储原语：同 key/同指纹重放首次响应，异指纹 COMMON_CONFLICT；无同 key 重放时已用绑定码仍 BIND_CODE_INVALID；绑定不自动授予 Agent 权限。复用 control.skill_import_idempotency，endpoint 固定 /internal/channel/bind；已核实包含租户/key/endpoint partial unique、request_fingerprint 与 response_json，不新增迁移或 Gateway 业务表。

### Checklist

- [ ] [B-103][integration] 修改生产代码前先按 真实 bind HTTP handler→PostgreSQL 幂等记录、bind_code 行锁、channel_identity 编写或扩展用例并记录 RED；关键断言：并发和重启后一次消费；租户/endpoint 隔离；响应重放；错误事务回滚；数据库保存 checksum 而非明文绑定码。执行 argv：`["uv","run","pytest","-q","tests/console_channel/test_channel_bind_idempotency.py","-k","b103"]`。
- [ ] 实现或补齐：在 Console Owner 事务内复用现有幂等存储原语：同 key/同指纹重放首次响应，异指纹 COMMON_CONFLICT；无同 key 重放时已用绑定码仍 BIND_CODE_INVALID；绑定不自动授予 Agent 权限。复用 control.skill_import_idempotency，endpoint 固定 /internal/channel/bind；已核实包含租户/key/endpoint partial unique、request_fingerprint 与 response_json，不新增迁移或 Gateway 业务表。
- [ ] 执行上述契约命令，填写 Acceptance Evidence 的 RED/GREEN、每个关键断言位置、真实组件与清理记录；失败、skip 或外部阻塞保留未验证。所有代码改动有对应测试，函数≤50行，强类型与显式异常处理。

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| B-103 | integration | 真实 bind HTTP handler→PostgreSQL 幂等记录、bind_code 行锁、channel_identity | 并发和重启后一次消费；租户/endpoint 隔离；响应重放；错误事务回滚；数据库保存 checksum 而非明文绑定码 | tests/console_channel/test_channel_bind_idempotency.py / B-103（planned） | `["uv","run","pytest","-q","tests/console_channel/test_channel_bind_idempotency.py","-k","b103"]` | planned |

### Acceptance Evidence
> planned。编码期填 RED/GREEN 命令与结果、断言路径/用例/位置、真实组件证据、外部依赖状态与清理证据；全部 verified 才可 done。本次结构检查不代表功能测试通过。

### Log
- [2026-09-20] prepared (draft)
- [2026-09-21] 用户确认后写入；设计修订已承接，状态保持 draft。

---

## TASK-004: 补齐 Console Effective Skills 内部端点

- **Status**: draft
- **Priority**: P0
- **Depends**: TASK-001, TASK-021
- **Source**: 10-im-gateway.backend.design.md#API-04 查询可用 Skills, 10-im-gateway.backend.design.md#2.5.1 业务规则与约束
- **Spec-Refs**: 
- **Acceptance-Refs**: B-104
- **Files**: `apps/console-platform/backend/src/muad_console_platform/api/internal_channel.py`, `apps/console-platform/backend/src/muad_console_platform/application/channel_skills_service.py`, `tests/console_channel/test_channel_skills_api.py`
- **Estimate**: 15–60 分钟；超出先拆分

### Description

补当前缺失的 GET /internal/channel/skills，复用 Effective Capability；仅返回 name/platform_label/description 等允许字段。按 API-04 的统一分页契约输出，含 enabled/is_deleted 与 SELECTED 用户授权过滤，不复制一套授权公式。

### Checklist

- [ ] [B-104][integration] 修改生产代码前先按 真实 Console handler→生产授权服务→PostgreSQL Agent/Skill/Grant 编写或扩展用例并记录 RED；关键断言：拒绝无 Agent 授权；禁用/删除/未授权 Skill 名称描述均不可见；分页边界与查询数量有界。执行 argv：`["uv","run","pytest","-q","tests/console_channel/test_channel_skills_api.py","-k","b104"]`。
- [ ] 实现或补齐：补当前缺失的 GET /internal/channel/skills，复用 Effective Capability；仅返回 name/platform_label/description 等允许字段。按 API-04 的统一分页契约输出，含 enabled/is_deleted 与 SELECTED 用户授权过滤，不复制一套授权公式。
- [ ] 执行上述契约命令，填写 Acceptance Evidence 的 RED/GREEN、每个关键断言位置、真实组件与清理记录；失败、skip 或外部阻塞保留未验证。所有代码改动有对应测试，函数≤50行，强类型与显式异常处理。

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| B-104 | integration | 真实 Console handler→生产授权服务→PostgreSQL Agent/Skill/Grant | 拒绝无 Agent 授权；禁用/删除/未授权 Skill 名称描述均不可见；分页边界与查询数量有界 | tests/console_channel/test_channel_skills_api.py / B-104（planned） | `["uv","run","pytest","-q","tests/console_channel/test_channel_skills_api.py","-k","b104"]` | planned |

### Acceptance Evidence
> planned。编码期填 RED/GREEN 命令与结果、断言路径/用例/位置、真实组件证据、外部依赖状态与清理证据；全部 verified 才可 done。本次结构检查不代表功能测试通过。

### Log
- [2026-09-20] prepared (draft)
- [2026-09-21] 用户确认后写入；设计修订已承接，状态保持 draft。

---

## TASK-005: 补 Bot 快照轮询与热更新边界

- **Status**: draft
- **Priority**: P0
- **Depends**: TASK-001, TASK-002, TASK-021
- **Source**: 10-im-gateway.backend.design.md#3.2.2 Bot 快照轮询与 Secret 解析, 10-im-gateway.backend.design.md#API-01 Bot 列表
- **Spec-Refs**: 
- **Acceptance-Refs**: B-105
- **Files**: `apps/im-gateway/src/muad_im_gateway/application/bot_snapshot.py`, `apps/console-platform/backend/src/muad_console_platform/api/internal_channel.py`, `tests/gateway/test_bot_snapshot.py`
- **Estimate**: 15–60 分钟；超出先拆分

### Description

保留启动全量、30s 轮询与 revision 更新；按 API-01 补快照分页，若 revision 跨页变化丢弃不完整快照并在下次节拍重拉；依赖故障保留最近完整快照，新增/停用/换 Agent/换 secret 只更新相关 bot。

### Checklist

- [ ] [B-105][integration] 修改生产代码前先按 Console snapshot HTTP→真实 PG bot 配置→BotSnapshotCache 编写或扩展用例并记录 RED；关键断言：revision 不变不重连；失败不清空；跨页版本不混合；原始 secret 只驻内存且不进入日志。执行 argv：`["uv","run","pytest","-q","tests/gateway/test_bot_snapshot.py","-k","b105"]`。
- [ ] 实现或补齐：保留启动全量、30s 轮询与 revision 更新；按 API-01 补快照分页，若 revision 跨页变化丢弃不完整快照并在下次节拍重拉；依赖故障保留最近完整快照，新增/停用/换 Agent/换 secret 只更新相关 bot。
- [ ] 执行上述契约命令，填写 Acceptance Evidence 的 RED/GREEN、每个关键断言位置、真实组件与清理记录；失败、skip 或外部阻塞保留未验证。所有代码改动有对应测试，函数≤50行，强类型与显式异常处理。

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| B-105 | integration | Console snapshot HTTP→真实 PG bot 配置→BotSnapshotCache | revision 不变不重连；失败不清空；跨页版本不混合；原始 secret 只驻内存且不进入日志 | tests/gateway/test_bot_snapshot.py / B-105（planned） | `["uv","run","pytest","-q","tests/gateway/test_bot_snapshot.py","-k","b105"]` | planned |

### Acceptance Evidence
> planned。编码期填 RED/GREEN 命令与结果、断言路径/用例/位置、真实组件证据、外部依赖状态与清理证据；全部 verified 才可 done。本次结构检查不代表功能测试通过。

### Log
- [2026-09-20] prepared (draft)
- [2026-09-21] 用户确认后写入；设计修订已承接，状态保持 draft。

---

## TASK-006: 修复多 Bot 故障隔离与 WS 退避

- **Status**: draft
- **Priority**: P0
- **Depends**: TASK-005, TASK-021
- **Source**: 10-im-gateway.backend.design.md#3.2.1 WebSocket 连接状态机, 10-im-gateway.backend.design.md#3.2.2 Bot 快照轮询与 Secret 解析
- **Spec-Refs**: 
- **Acceptance-Refs**: B-106
- **Files**: `apps/im-gateway/src/muad_im_gateway/channels/wecom/adapter.py`, `tests/gateway/test_wecom_adapter.py`
- **Estimate**: 15–60 分钟；超出先拆分

### Description

修复任一 bot secret 失败导致 stop 全部 bot 的路径；实现带 jitter 的有界指数退避、单连接 STOPPING 清理、可取消等待；保留 iter_events() 唯一规范化路径。

### Checklist

- [ ] [B-106][integration] 修改生产代码前先按 生产 WeComAdapter/连接管理器→真实本地 WS 故障探针 编写或扩展用例并记录 RED；关键断言：坏 bot 重试不影响好 bot；停用只停止目标连接；任务无泄漏；握手/断线可恢复；禁止紧循环。执行 argv：`["uv","run","pytest","-q","tests/gateway/test_wecom_adapter.py","-k","b106"]`。
- [ ] 实现或补齐：修复任一 bot secret 失败导致 stop 全部 bot 的路径；实现带 jitter 的有界指数退避、单连接 STOPPING 清理、可取消等待；保留 iter_events() 唯一规范化路径。
- [ ] 执行上述契约命令，填写 Acceptance Evidence 的 RED/GREEN、每个关键断言位置、真实组件与清理记录；失败、skip 或外部阻塞保留未验证。所有代码改动有对应测试，函数≤50行，强类型与显式异常处理。

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| B-106 | integration | 生产 WeComAdapter/连接管理器→真实本地 WS 故障探针 | 坏 bot 重试不影响好 bot；停用只停止目标连接；任务无泄漏；握手/断线可恢复；禁止紧循环 | tests/gateway/test_wecom_adapter.py / B-106（planned） | `["uv","run","pytest","-q","tests/gateway/test_wecom_adapter.py","-k","b106"]` | planned |

### Acceptance Evidence
> planned。编码期填 RED/GREEN 命令与结果、断言路径/用例/位置、真实组件证据、外部依赖状态与清理证据；全部 verified 才可 done。本次结构检查不代表功能测试通过。

### Log
- [2026-09-20] prepared (draft)
- [2026-09-21] 用户确认后写入；设计修订已承接，状态保持 draft。

---

## TASK-007: 对齐启动、就绪与关闭语义

- **Status**: draft
- **Priority**: P0
- **Depends**: TASK-005, TASK-006, TASK-021
- **Source**: 10-im-gateway.backend.design.md#4.1 健康检查与启动校验, 10-im-gateway.backend.design.md#3.2.1 WebSocket 连接状态机
- **Spec-Refs**: 
- **Acceptance-Refs**: B-107
- **Files**: `apps/im-gateway/src/muad_im_gateway/main.py`, `apps/im-gateway/src/muad_im_gateway/api/health.py`, `tests/gateway/test_readyz.py`
- **Estimate**: 15–60 分钟；超出先拆分

### Description

复用 api-kit 探针与启动校验，区分 manager 未初始化与单 bot BACKOFF；有有效快照时 Console 短暂不可达可服务，单 bot 故障记录 degraded 详情。初始化失败清理已创建资源，进程关闭取消并等待消费/轮询任务。

### Checklist

- [ ] [B-107][integration] 修改生产代码前先按 真实 Gateway lifespan/HTTP probes→Console/WS 连接管理器 编写或扩展用例并记录 RED；关键断言：healthz 仅存活；缺启动必需条件 readyz=503；正常 manager 不要求全部 bot CONNECTED；关闭不遗留任务/连接。执行 argv：`["uv","run","pytest","-q","tests/gateway/test_readyz.py","-k","b107"]`。
- [ ] 实现或补齐：复用 api-kit 探针与启动校验，区分 manager 未初始化与单 bot BACKOFF；有有效快照时 Console 短暂不可达可服务，单 bot 故障记录 degraded 详情。初始化失败清理已创建资源，进程关闭取消并等待消费/轮询任务。
- [ ] 执行上述契约命令，填写 Acceptance Evidence 的 RED/GREEN、每个关键断言位置、真实组件与清理记录；失败、skip 或外部阻塞保留未验证。所有代码改动有对应测试，函数≤50行，强类型与显式异常处理。

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| B-107 | integration | 真实 Gateway lifespan/HTTP probes→Console/WS 连接管理器 | healthz 仅存活；缺启动必需条件 readyz=503；正常 manager 不要求全部 bot CONNECTED；关闭不遗留任务/连接 | tests/gateway/test_readyz.py / B-107（planned） | `["uv","run","pytest","-q","tests/gateway/test_readyz.py","-k","b107"]` | planned |

### Acceptance Evidence
> planned。编码期填 RED/GREEN 命令与结果、断言路径/用例/位置、真实组件证据、外部依赖状态与清理证据；全部 verified 才可 done。本次结构检查不代表功能测试通过。

### Log
- [2026-09-20] prepared (draft)
- [2026-09-21] 用户确认后写入；设计修订已承接，状态保持 draft。

---

## TASK-008: 验证入站 Redis 原子去重与降级

- **Status**: draft
- **Priority**: P0
- **Depends**: TASK-001, TASK-021
- **Source**: 10-im-gateway.backend.design.md#3.2.3 入站去重
- **Spec-Refs**: 
- **Acceptance-Refs**: B-108, S-05, RULE-08
- **Files**: `apps/im-gateway/src/muad_im_gateway/infrastructure/dedupe.py`, `apps/im-gateway/src/muad_im_gateway/application/inbound.py`, `tests/gateway/test_inbound_dedupe_integration.py`
- **Estimate**: 15–60 分钟；超出先拆分

### Description

沿用 SET NX EX 600，在解析、resolve 与创建 Run 前判重；用真实 Redis 覆盖并发、TTL 和恢复，Redis 断线继续 at-least-once，不以进程内 set 冒充跨副本去重。

### Checklist

- [ ] [B-108][integration] 修改生产代码前先按 Gateway inbound→真实 Redis→真实 Runtime HTTP 接收边界 编写或扩展用例并记录 RED；关键断言：相同 channel/message_id 只首次下游调用；重复 ACK/忽略；TTL=600；Redis 故障继续处理；message_id 不变。执行 argv：`["uv","run","pytest","-q","tests/gateway/test_inbound_dedupe_integration.py","-k","b108"]`。
- [ ] [S-05][integration] 修改生产代码前先按 Gateway入站→真实Redis dedupe→真实下游HTTP观测 编写或扩展用例并记录 RED；关键断言：SET NX EX600；第二次ACK/忽略且不创建第二Run。执行 argv：`["uv","run","pytest","-q","tests/gateway/test_inbound_dedupe_integration.py","-k","s05"]`。
- [ ] 实现或补齐：沿用 SET NX EX 600，在解析、resolve 与创建 Run 前判重；用真实 Redis 覆盖并发、TTL 和恢复，Redis 断线继续 at-least-once，不以进程内 set 冒充跨副本去重。
- [ ] [RULE-08][integration] 作为唯一最终负责人，沿 Gateway inbound→真实 Redis→真实 Runtime HTTP 接收边界 验证 入站Redis SET NX EX600、降级at-least-once；联合映射 S-05 / E-06；命令 `["uv","run","pytest","-q","tests/gateway/test_inbound_dedupe_integration.py","-k","b108"]`，不得以任务标题或静态声明代替行为证据。
- [ ] 执行上述契约命令，填写 Acceptance Evidence 的 RED/GREEN、每个关键断言位置、真实组件与清理记录；失败、skip 或外部阻塞保留未验证。所有代码改动有对应测试，函数≤50行，强类型与显式异常处理。

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| B-108 | integration | Gateway inbound→真实 Redis→真实 Runtime HTTP 接收边界 | 相同 channel/message_id 只首次下游调用；重复 ACK/忽略；TTL=600；Redis 故障继续处理；message_id 不变 | tests/gateway/test_inbound_dedupe_integration.py / B-108（planned） | `["uv","run","pytest","-q","tests/gateway/test_inbound_dedupe_integration.py","-k","b108"]` | planned |
| S-05 | integration | Gateway入站→真实Redis dedupe→真实下游HTTP观测 | SET NX EX600；第二次ACK/忽略且不创建第二Run | tests/gateway/test_inbound_dedupe_integration.py / S-05（planned） | `["uv","run","pytest","-q","tests/gateway/test_inbound_dedupe_integration.py","-k","s05"]` | planned |
| RULE-08 | integration | Gateway inbound→真实 Redis→真实 Runtime HTTP 接收边界 | 入站Redis SET NX EX600、降级at-least-once；联合映射 S-05 / E-06 | tests/gateway/test_inbound_dedupe_integration.py / RULE-08（planned） | `["uv","run","pytest","-q","tests/gateway/test_inbound_dedupe_integration.py","-k","b108"]` | planned |

### Acceptance Evidence
> planned。编码期填 RED/GREEN 命令与结果、断言路径/用例/位置、真实组件证据、外部依赖状态与清理证据；全部 verified 才可 done。本次结构检查不代表功能测试通过。

### Log
- [2026-09-20] prepared (draft)
- [2026-09-21] 用户确认后写入；设计修订已承接，状态保持 draft。

---

## TASK-009: 对齐 resolve、未绑定与授权分流

- **Status**: draft
- **Priority**: P0
- **Depends**: TASK-001, TASK-002, TASK-021
- **Source**: 10-im-gateway.backend.design.md#API-02 解析消息路由, 10-im-gateway.backend.design.md#API-06 Runtime Run 桥接
- **Spec-Refs**: 
- **Acceptance-Refs**: B-109, E-01
- **Files**: `apps/im-gateway/src/muad_im_gateway/application/inbound.py`, `tests/gateway/test_message_routing_integration.py`
- **Estimate**: 15–60 分钟；超出先拆分

### Description

透传 conversation 标识，按 bound/authorized 明确分流；未绑定给绑定提示，禁用/不存在 bot 使用 BOT_NOT_FOUND；只传 logical agent_id，不保存 Agent→Pod 或 active run 映射。

### Checklist

- [ ] [B-109][integration] 修改生产代码前先按 Gateway→真实 Console resolve HTTP→PostgreSQL→Runtime 接收观测 编写或扩展用例并记录 RED；关键断言：bound=false 为正常分支；未绑定/无授权/禁用 bot 无 Run；同用户跨 bot 的 route 不串线。执行 argv：`["uv","run","pytest","-q","tests/gateway/test_message_routing_integration.py","-k","b109"]`。
- [ ] [E-01][integration] 修改生产代码前先按 Gateway→真实bot resolve HTTP→PostgreSQL 编写或扩展用例并记录 RED；关键断言：bot未知/禁用返回BOT_NOT_FOUND；不创建Run。执行 argv：`["uv","run","pytest","-q","tests/gateway/test_message_routing_integration.py","-k","e01"]`。
- [ ] 实现或补齐：透传 conversation 标识，按 bound/authorized 明确分流；未绑定给绑定提示，禁用/不存在 bot 使用 BOT_NOT_FOUND；只传 logical agent_id，不保存 Agent→Pod 或 active run 映射。
- [ ] 执行上述契约命令，填写 Acceptance Evidence 的 RED/GREEN、每个关键断言位置、真实组件与清理记录；失败、skip 或外部阻塞保留未验证。所有代码改动有对应测试，函数≤50行，强类型与显式异常处理。

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| B-109 | integration | Gateway→真实 Console resolve HTTP→PostgreSQL→Runtime 接收观测 | bound=false 为正常分支；未绑定/无授权/禁用 bot 无 Run；同用户跨 bot 的 route 不串线 | tests/gateway/test_message_routing_integration.py / B-109（planned） | `["uv","run","pytest","-q","tests/gateway/test_message_routing_integration.py","-k","b109"]` | planned |
| E-01 | integration | Gateway→真实bot resolve HTTP→PostgreSQL | bot未知/禁用返回BOT_NOT_FOUND；不创建Run | tests/gateway/test_message_routing_integration.py / E-01（planned） | `["uv","run","pytest","-q","tests/gateway/test_message_routing_integration.py","-k","e01"]` | planned |

### Acceptance Evidence
> planned。编码期填 RED/GREEN 命令与结果、断言路径/用例/位置、真实组件证据、外部依赖状态与清理证据；全部 verified 才可 done。本次结构检查不代表功能测试通过。

### Log
- [2026-09-20] prepared (draft)
- [2026-09-21] 用户确认后写入；设计修订已承接，状态保持 draft。

---

## TASK-010: 完善 /bind 命令与稳定幂等键

- **Status**: draft
- **Priority**: P0
- **Depends**: TASK-002, TASK-003, TASK-021
- **Source**: 10-im-gateway.backend.design.md#3.4.2 内置命令与文案映射（FEAT-02）, 10-im-gateway.backend.design.md#API-03 执行绑定
- **Spec-Refs**: 
- **Acceptance-Refs**: B-110
- **Files**: `apps/im-gateway/src/muad_im_gateway/application/inbound.py`, `apps/im-gateway/src/muad_im_gateway/application/console_client.py`, `tests/gateway/test_bind_command.py`
- **Estimate**: 15–60 分钟；超出先拆分

### Description

本地识别 /bind 与缺参数提示，不发送 LLM；将原 channel message_id 作为稳定 Idempotency-Key 传 Console；成功、无效、过期与已消费分支使用设计文案和 error catalog。

### Checklist

- [ ] [B-110][integration] 修改生产代码前先按 Gateway command→真实 Console bind HTTP→PostgreSQL 编写或扩展用例并记录 RED；关键断言：绑定成功回复且身份可回读；错误码准确；重投消息不重新消费；bind code 不出日志/Runtime 请求。执行 argv：`["uv","run","pytest","-q","tests/gateway/test_bind_command.py","-k","b110"]`。
- [ ] 实现或补齐：本地识别 /bind 与缺参数提示，不发送 LLM；将原 channel message_id 作为稳定 Idempotency-Key 传 Console；成功、无效、过期与已消费分支使用设计文案和 error catalog。
- [ ] 执行上述契约命令，填写 Acceptance Evidence 的 RED/GREEN、每个关键断言位置、真实组件与清理记录；失败、skip 或外部阻塞保留未验证。所有代码改动有对应测试，函数≤50行，强类型与显式异常处理。

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| B-110 | integration | Gateway command→真实 Console bind HTTP→PostgreSQL | 绑定成功回复且身份可回读；错误码准确；重投消息不重新消费；bind code 不出日志/Runtime 请求 | tests/gateway/test_bind_command.py / B-110（planned） | `["uv","run","pytest","-q","tests/gateway/test_bind_command.py","-k","b110"]` | planned |

### Acceptance Evidence
> planned。编码期填 RED/GREEN 命令与结果、断言路径/用例/位置、真实组件证据、外部依赖状态与清理证据；全部 verified 才可 done。本次结构检查不代表功能测试通过。

### Log
- [2026-09-20] prepared (draft)
- [2026-09-21] 用户确认后写入；设计修订已承接，状态保持 draft。

---

## TASK-011: 对齐 /skills 与 /new 命令输出

- **Status**: draft
- **Priority**: P0
- **Depends**: TASK-002, TASK-004, TASK-009, TASK-013, TASK-021
- **Source**: 10-im-gateway.backend.design.md#3.4.2 内置命令与文案映射（FEAT-02）, 10-im-gateway.backend.design.md#API-04 查询可用 Skills
- **Spec-Refs**: 
- **Acceptance-Refs**: B-111
- **Files**: `apps/im-gateway/src/muad_im_gateway/application/inbound.py`, `apps/im-gateway/src/muad_im_gateway/application/console_client.py`, `tests/gateway/test_commands_integration.py`
- **Estimate**: 15–60 分钟；超出先拆分
- **External-Depends**: EXT-08（真实 Runtime 契约与对应验收 Evidence）

### Description

Skills 文案保留 name、platform_label、description，不输出全文；按 API-04 分页契约获取完整有界目录。/new 调用 Runtime conversations，不修改身份、Agent 或 Memory；后续普通消息由 Runtime 找到新会话。

### Checklist

- [ ] [B-111][integration] 修改生产代码前先按 Gateway commands→真实 Console/Runtime HTTP→PostgreSQL 编写或扩展用例并记录 RED；关键断言：空目录与错误有区别；未授权条目不泄露；/new 生成新 Conversation，旧 Memory/绑定不变；命令不进 LLM。执行 argv：`["uv","run","pytest","-q","tests/gateway/test_commands_integration.py","-k","b111"]`。
- [ ] 实现或补齐：Skills 文案保留 name、platform_label、description，不输出全文；按 API-04 分页契约获取完整有界目录。/new 调用 Runtime conversations，不修改身份、Agent 或 Memory；后续普通消息由 Runtime 找到新会话。
- [ ] 执行上述契约命令，填写 Acceptance Evidence 的 RED/GREEN、每个关键断言位置、真实组件与清理记录；失败、skip 或外部阻塞保留未验证。所有代码改动有对应测试，函数≤50行，强类型与显式异常处理。

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| B-111 | integration | Gateway commands→真实 Console/Runtime HTTP→PostgreSQL | 空目录与错误有区别；未授权条目不泄露；/new 生成新 Conversation，旧 Memory/绑定不变；命令不进 LLM | tests/gateway/test_commands_integration.py / B-111（planned） | `["uv","run","pytest","-q","tests/gateway/test_commands_integration.py","-k","b111"]` | planned |

### Acceptance Evidence
> planned。编码期填 RED/GREEN 命令与结果、断言路径/用例/位置、真实组件证据、外部依赖状态与清理证据；全部 verified 才可 done。本次结构检查不代表功能测试通过。

### Log
- [2026-09-20] prepared (draft)
- [2026-09-21] 用户确认后写入；设计修订已承接，状态保持 draft。

---

## TASK-012: 修复 /stop 已取消与取消中文案

- **Status**: draft
- **Priority**: P0
- **Depends**: TASK-009, TASK-013, TASK-021
- **Source**: 10-im-gateway.backend.design.md#3.4.2 内置命令与文案映射（FEAT-02）
- **Spec-Refs**: 
- **Acceptance-Refs**: B-112, S-06, E-05
- **Files**: `apps/im-gateway/src/muad_im_gateway/application/inbound.py`, `tests/gateway/test_stop_integration.py`
- **Estimate**: 15–60 分钟；超出先拆分
- **External-Depends**: EXT-08（真实 Runtime 契约与对应验收 Evidence）

### Description

解析 cancel-active 返回状态：WAITING_INPUT 已 CAS CANCELLED 立即显示已停止，CREATED/RUNNING 的 CANCELLING 显示受理，NO_ACTIVE_RUN 显示无执行中任务；不由 Gateway 猜测或缓存活跃 Run。

### Checklist

- [ ] [B-112][integration] 修改生产代码前先按 Gateway→真实 Runtime cancel-active HTTP→PostgreSQL CAS/事件 编写或扩展用例并记录 RED；关键断言：WAITING_INPUT 终态及取消事件可回读；RUNNING 受理不冒充已完成；无活跃 404/NO_ACTIVE_RUN；权限拒绝不发送取消成功。执行 argv：`["uv","run","pytest","-q","tests/gateway/test_stop_integration.py","-k","b112"]`。
- [ ] [S-06][integration] 修改生产代码前先按 真实Gateway→Runtime cancel-active HTTP→PostgreSQL CAS 编写或扩展用例并记录 RED；关键断言：WAITING_INPUT直接CANCELLED；interrupt取消；回复当前任务已停止。执行 argv：`["uv","run","pytest","-q","tests/gateway/test_stop_integration.py","-k","s06"]`。
- [ ] [E-05][integration] 修改生产代码前先按 真实Runtime cancel-active/PG→Gateway 编写或扩展用例并记录 RED；关键断言：无活跃返回NO_ACTIVE_RUN；提示当前没有执行中的任务。执行 argv：`["uv","run","pytest","-q","tests/gateway/test_stop_integration.py","-k","e05"]`。
- [ ] 实现或补齐：解析 cancel-active 返回状态：WAITING_INPUT 已 CAS CANCELLED 立即显示已停止，CREATED/RUNNING 的 CANCELLING 显示受理，NO_ACTIVE_RUN 显示无执行中任务；不由 Gateway 猜测或缓存活跃 Run。
- [ ] 执行上述契约命令，填写 Acceptance Evidence 的 RED/GREEN、每个关键断言位置、真实组件与清理记录；失败、skip 或外部阻塞保留未验证。所有代码改动有对应测试，函数≤50行，强类型与显式异常处理。

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| B-112 | integration | Gateway→真实 Runtime cancel-active HTTP→PostgreSQL CAS/事件 | WAITING_INPUT 终态及取消事件可回读；RUNNING 受理不冒充已完成；无活跃 404/NO_ACTIVE_RUN；权限拒绝不发送取消成功 | tests/gateway/test_stop_integration.py / B-112（planned） | `["uv","run","pytest","-q","tests/gateway/test_stop_integration.py","-k","b112"]` | planned |
| S-06 | integration | 真实Gateway→Runtime cancel-active HTTP→PostgreSQL CAS | WAITING_INPUT直接CANCELLED；interrupt取消；回复当前任务已停止 | tests/gateway/test_stop_integration.py / S-06（planned） | `["uv","run","pytest","-q","tests/gateway/test_stop_integration.py","-k","s06"]` | planned |
| E-05 | integration | 真实Runtime cancel-active/PG→Gateway | 无活跃返回NO_ACTIVE_RUN；提示当前没有执行中的任务 | tests/gateway/test_stop_integration.py / E-05（planned） | `["uv","run","pytest","-q","tests/gateway/test_stop_integration.py","-k","e05"]` | planned |

### Acceptance Evidence
> planned。编码期填 RED/GREEN 命令与结果、断言路径/用例/位置、真实组件证据、外部依赖状态与清理证据；全部 verified 才可 done。本次结构检查不代表功能测试通过。

### Log
- [2026-09-20] prepared (draft)
- [2026-09-21] 用户确认后写入；设计修订已承接，状态保持 draft。

---

## TASK-013: 补 Runtime 客户端幂等头与请求上下文

- **Status**: draft
- **Priority**: P0
- **Depends**: TASK-001, TASK-021
- **Source**: 10-im-gateway.backend.design.md#API-06 Runtime Run 桥接, 10-im-gateway.backend.design.md#3.4.2 内置命令与文案映射（FEAT-02）
- **Spec-Refs**: 
- **Acceptance-Refs**: B-113
- **Files**: `apps/im-gateway/src/muad_im_gateway/application/runtime_client.py`, `tests/gateway/test_runtime_client.py`
- **Estimate**: 15–60 分钟；超出先拆分

### Description

create_run 使用原 message.id 作 Idempotency-Key；透传 tenant/trace/request，保持逻辑 Agent 路由；新增会话的可重试提交按 Owner 端支持的稳定 key 契约接入，依赖 Runtime 补齐时记录外部阻塞。

### Checklist

- [ ] [B-113][integration] 修改生产代码前先按 生产 RuntimeClient→真实本地 HTTP/SSE 接收端 编写或扩展用例并记录 RED；关键断言：请求 key/上下文不丢；超时有界；正常 SSE 与错误 Envelope 正确区分；不出现 pod_id。执行 argv：`["uv","run","pytest","-q","tests/gateway/test_runtime_client.py","-k","b113"]`。
- [ ] 实现或补齐：create_run 使用原 message.id 作 Idempotency-Key；透传 tenant/trace/request，保持逻辑 Agent 路由；新增会话的可重试提交按 Owner 端支持的稳定 key 契约接入，依赖 Runtime 补齐时记录外部阻塞。
- [ ] 执行上述契约命令，填写 Acceptance Evidence 的 RED/GREEN、每个关键断言位置、真实组件与清理记录；失败、skip 或外部阻塞保留未验证。所有代码改动有对应测试，函数≤50行，强类型与显式异常处理。

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| B-113 | integration | 生产 RuntimeClient→真实本地 HTTP/SSE 接收端 | 请求 key/上下文不丢；超时有界；正常 SSE 与错误 Envelope 正确区分；不出现 pod_id | tests/gateway/test_runtime_client.py / B-113（planned） | `["uv","run","pytest","-q","tests/gateway/test_runtime_client.py","-k","b113"]` | planned |

### Acceptance Evidence
> planned。编码期填 RED/GREEN 命令与结果、断言路径/用例/位置、真实组件证据、外部依赖状态与清理证据；全部 verified 才可 done。本次结构检查不代表功能测试通过。

### Log
- [2026-09-20] prepared (draft)
- [2026-09-21] 用户确认后写入；设计修订已承接，状态保持 draft。

---

## TASK-014: 使 SSE 解析保留封套与序号

- **Status**: draft
- **Priority**: P0
- **Depends**: TASK-013
- **Source**: 10-im-gateway.backend.design.md#3.4.1 Runtime SSE 事件处理（FEAT-03）
- **Spec-Refs**: 
- **Acceptance-Refs**: B-114
- **Files**: `apps/im-gateway/src/muad_im_gateway/application/runtime_client.py`, `apps/im-gateway/src/muad_im_gateway/application/sse.py`, `tests/gateway/test_sse_parser.py`
- **Estimate**: 15–60 分钟；超出先拆分

### Description

抽出强类型 SSE 事件，保留 run_id/seq/timestamp/type/data；处理 CRLF、多行 data、网络分片与注释 heartbeat；错误帧显式记录并安全收尾，重复/乱序帧不重复输出。

### Checklist

- [ ] [B-114][unit] 修改生产代码前先按 真实 SSE parser 与分片字节/行输入 编写或扩展用例并记录 RED；关键断言：seq 严格单调；heartbeat 不计 seq；run.created resumed 字段保留；非法 JSON/未知事件不损坏流状态。执行 argv：`["uv","run","pytest","-q","tests/gateway/test_sse_parser.py","-k","b114"]`。
- [ ] 实现或补齐：抽出强类型 SSE 事件，保留 run_id/seq/timestamp/type/data；处理 CRLF、多行 data、网络分片与注释 heartbeat；错误帧显式记录并安全收尾，重复/乱序帧不重复输出。
- [ ] 执行上述契约命令，填写 Acceptance Evidence 的 RED/GREEN、每个关键断言位置、真实组件与清理记录；失败、skip 或外部阻塞保留未验证。所有代码改动有对应测试，函数≤50行，强类型与显式异常处理。

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| B-114 | unit | 真实 SSE parser 与分片字节/行输入 | seq 严格单调；heartbeat 不计 seq；run.created resumed 字段保留；非法 JSON/未知事件不损坏流状态 | tests/gateway/test_sse_parser.py / B-114（planned） | `["uv","run","pytest","-q","tests/gateway/test_sse_parser.py","-k","b114"]` | planned |

### Acceptance Evidence
> planned。编码期填 RED/GREEN 命令与结果、断言路径/用例/位置、真实组件证据、外部依赖状态与清理证据；全部 verified 才可 done。本次结构检查不代表功能测试通过。

### Log
- [2026-09-20] prepared (draft)
- [2026-09-21] 用户确认后写入；设计修订已承接，状态保持 draft。

---

## TASK-015: 补齐 SSE 到 IM 的收尾与中断呈现

- **Status**: draft
- **Priority**: P0
- **Depends**: TASK-014, TASK-021
- **Source**: 10-im-gateway.backend.design.md#3.4.1 Runtime SSE 事件处理（FEAT-03）, 10-im-gateway.backend.design.md#3.4.2 内置命令与文案映射（FEAT-02）
- **Spec-Refs**: 
- **Acceptance-Refs**: B-115
- **Files**: `apps/im-gateway/src/muad_im_gateway/application/inbound.py`, `apps/im-gateway/src/muad_im_gateway/application/stream_renderer.py`, `tests/gateway/test_stream_renderer.py`
- **Estimate**: 15–60 分钟；超出先拆分

### Description

按 SDK 最小间隔节流，interrupt 先 flush 再输出 prompt/options；task.accepted、completed、failed 只 finalize 一次；CANCELLED 显示已停止，RUN_ABANDONED 按终态文案处理；Artifact 只给摘要。新模块保持单函数≤50行。

### Checklist

- [ ] [B-115][integration] 修改生产代码前先按 真实 SSE 解析→生产 renderer→真实本地 WS SDK 出站 编写或扩展用例并记录 RED；关键断言：无双重 finalize/尾段丢失；CANCELLED/受理/异常文案准确；无 Artifact 下载链接或内部 Tool/secret 信息。执行 argv：`["uv","run","pytest","-q","tests/gateway/test_stream_renderer.py","-k","b115"]`。
- [ ] 实现或补齐：按 SDK 最小间隔节流，interrupt 先 flush 再输出 prompt/options；task.accepted、completed、failed 只 finalize 一次；CANCELLED 显示已停止，RUN_ABANDONED 按终态文案处理；Artifact 只给摘要。新模块保持单函数≤50行。
- [ ] 执行上述契约命令，填写 Acceptance Evidence 的 RED/GREEN、每个关键断言位置、真实组件与清理记录；失败、skip 或外部阻塞保留未验证。所有代码改动有对应测试，函数≤50行，强类型与显式异常处理。

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| B-115 | integration | 真实 SSE 解析→生产 renderer→真实本地 WS SDK 出站 | 无双重 finalize/尾段丢失；CANCELLED/受理/异常文案准确；无 Artifact 下载链接或内部 Tool/secret 信息 | tests/gateway/test_stream_renderer.py / B-115（planned） | `["uv","run","pytest","-q","tests/gateway/test_stream_renderer.py","-k","b115"]` | planned |

### Acceptance Evidence
> planned。编码期填 RED/GREEN 命令与结果、断言路径/用例/位置、真实组件证据、外部依赖状态与清理证据；全部 verified 才可 done。本次结构检查不代表功能测试通过。

### Log
- [2026-09-20] prepared (draft)
- [2026-09-21] 用户确认后写入；设计修订已承接，状态保持 draft。

---

## TASK-016: 避免长流阻塞后续消息与 /stop

- **Status**: draft
- **Priority**: P0
- **Depends**: TASK-009, TASK-010, TASK-011, TASK-012, TASK-015, TASK-021
- **Source**: 10-im-gateway.backend.design.md#3.2 架构与流程, 10-im-gateway.backend.design.md#API-06 Runtime Run 桥接, 10-im-gateway.backend.design.md#3.4.1 Runtime SSE 事件处理（FEAT-03）
- **Spec-Refs**: 
- **Acceptance-Refs**: B-116
- **Files**: `apps/im-gateway/src/muad_im_gateway/application/inbound.py`, `apps/im-gateway/src/muad_im_gateway/channels/wecom/adapter.py`, `tests/gateway/test_inbound_concurrency.py`
- **Estimate**: 15–60 分钟；超出先拆分

### Description

用有界消费任务与 route 级流管理避免一个 Run 阻塞全 bot 消息迭代；/stop 可在长流期间处理；清除持久 _pending_run_ids，下一条普通消息由 Runtime 自动 resume，关闭可取消所有消费者。

### Checklist

- [ ] [B-116][integration] 修改生产代码前先按 真实 iter_events→Gateway 消费队列→Runtime HTTP/SSE→WS 回复 编写或扩展用例并记录 RED；关键断言：一个长流不阻塞另一用户/停止命令；同 route 流不串；Runtime 决定 RUN_BUSY/resume；无本地活跃 Run 事实缓存。执行 argv：`["uv","run","pytest","-q","tests/gateway/test_inbound_concurrency.py","-k","b116"]`。
- [ ] 实现或补齐：用有界消费任务与 route 级流管理避免一个 Run 阻塞全 bot 消息迭代；/stop 可在长流期间处理；清除持久 _pending_run_ids，下一条普通消息由 Runtime 自动 resume，关闭可取消所有消费者。
- [ ] 执行上述契约命令，填写 Acceptance Evidence 的 RED/GREEN、每个关键断言位置、真实组件与清理记录；失败、skip 或外部阻塞保留未验证。所有代码改动有对应测试，函数≤50行，强类型与显式异常处理。

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| B-116 | integration | 真实 iter_events→Gateway 消费队列→Runtime HTTP/SSE→WS 回复 | 一个长流不阻塞另一用户/停止命令；同 route 流不串；Runtime 决定 RUN_BUSY/resume；无本地活跃 Run 事实缓存 | tests/gateway/test_inbound_concurrency.py / B-116（planned） | `["uv","run","pytest","-q","tests/gateway/test_inbound_concurrency.py","-k","b116"]` | planned |

### Acceptance Evidence
> planned。编码期填 RED/GREEN 命令与结果、断言路径/用例/位置、真实组件证据、外部依赖状态与清理证据；全部 verified 才可 done。本次结构检查不代表功能测试通过。

### Log
- [2026-09-20] prepared (draft)
- [2026-09-21] 用户确认后写入；设计修订已承接，状态保持 draft。

---

## TASK-017: 对齐主动投递响应与渠道错误

- **Status**: draft
- **Priority**: P0
- **Depends**: TASK-001, TASK-006, TASK-021
- **Source**: 10-im-gateway.backend.design.md#API-05 主动投递
- **Spec-Refs**: 
- **Acceptance-Refs**: B-117
- **Files**: `apps/im-gateway/src/muad_im_gateway/api/delivery.py`, `tests/gateway/test_delivery_api.py`
- **Estimate**: 15–60 分钟；超出先拆分
- **External-Depends**: EXT-09-020, EXT-09-021（最终 S-04 同时对照 EXT-09-043）

### Description

在 EXT-09-021 去重实现完成后统一 accepted/deduplicated 响应；未配置/禁用 bot 使用 BOT_NOT_FOUND，非法 route/message 使用 COMMON_VALIDATION_ERROR；artifact_ids 不转换为 IM 下载入口。原子去重、发送失败恢复与 Redis 降级实现由 EXT-09-021 唯一承担。

### Checklist

- [ ] [B-117][integration] 修改生产代码前先按 真实 Gateway HTTP→生产 Adapter→真实 Redis/本地 WS 编写或扩展用例并记录 RED；关键断言：重复200且deduplicated=true；失败不是accepted；按route选择bot；无重新Agent reasoning；沿用7d去重。执行 argv：`["uv","run","pytest","-q","tests/gateway/test_delivery_api.py","-k","b117"]`。
- [ ] 实现或补齐：在 EXT-09-021 去重实现完成后统一 accepted/deduplicated 响应；未配置/禁用 bot 使用 BOT_NOT_FOUND，非法 route/message 使用 COMMON_VALIDATION_ERROR；artifact_ids 不转换为 IM 下载入口。原子去重、发送失败恢复与 Redis 降级实现由 EXT-09-021 唯一承担。
- [ ] 执行上述契约命令，填写 Acceptance Evidence 的 RED/GREEN、每个关键断言位置、真实组件与清理记录；失败、skip 或外部阻塞保留未验证。所有代码改动有对应测试，函数≤50行，强类型与显式异常处理。

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| B-117 | integration | 真实 Gateway HTTP→生产 Adapter→真实 Redis/本地 WS | 重复200且deduplicated=true；失败不是accepted；按route选择bot；无重新Agent reasoning；沿用7d去重 | tests/gateway/test_delivery_api.py / B-117（planned） | `["uv","run","pytest","-q","tests/gateway/test_delivery_api.py","-k","b117"]` | planned |

### Acceptance Evidence
> planned。编码期填 RED/GREEN 命令与结果、断言路径/用例/位置、真实组件证据、外部依赖状态与清理证据；全部 verified 才可 done。本次结构检查不代表功能测试通过。

### Log
- [2026-09-20] prepared (draft)
- [2026-09-21] 用户确认后写入；设计修订已承接，状态保持 draft。

---

## TASK-018: 补连接状态指标与脱敏日志

- **Status**: draft
- **Priority**: P1
- **Depends**: TASK-006, TASK-007, TASK-021
- **Source**: 10-im-gateway.backend.design.md#4.2 指标目录, 10-im-gateway.backend.design.md#3.2.1 WebSocket 连接状态机, 10-im-gateway.backend.design.md#3.5 质量实现方案
- **Spec-Refs**: 
- **Acceptance-Refs**: B-118
- **Files**: `apps/im-gateway/src/muad_im_gateway/infrastructure/metrics.py`, `apps/im-gateway/src/muad_im_gateway/channels/wecom/adapter.py`, `tests/gateway/test_connection_observability.py`
- **Estimate**: 15–60 分钟；超出先拆分

### Description

通过现有可观测设施导出 wecom_ws_connected 与状态转换日志，记录 bot_id/from/to/attempt/trace；日志参数不得包含 SDK 原始异常密钥，标签不含消息正文。

### Checklist

- [ ] [B-118][integration] 修改生产代码前先按 真实连接迁移→生产日志/metrics exporter 编写或扩展用例并记录 RED；关键断言：连通/退避/停止指标随状态变化；坏 bot 不影响其他序列；日志字段完整且无 secret canary。执行 argv：`["uv","run","pytest","-q","tests/gateway/test_connection_observability.py","-k","b118"]`。
- [ ] 实现或补齐：通过现有可观测设施导出 wecom_ws_connected 与状态转换日志，记录 bot_id/from/to/attempt/trace；日志参数不得包含 SDK 原始异常密钥，标签不含消息正文。
- [ ] 执行上述契约命令，填写 Acceptance Evidence 的 RED/GREEN、每个关键断言位置、真实组件与清理记录；失败、skip 或外部阻塞保留未验证。所有代码改动有对应测试，函数≤50行，强类型与显式异常处理。

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| B-118 | integration | 真实连接迁移→生产日志/metrics exporter | 连通/退避/停止指标随状态变化；坏 bot 不影响其他序列；日志字段完整且无 secret canary | tests/gateway/test_connection_observability.py / B-118（planned） | `["uv","run","pytest","-q","tests/gateway/test_connection_observability.py","-k","b118"]` | planned |

### Acceptance Evidence
> planned。编码期填 RED/GREEN 命令与结果、断言路径/用例/位置、真实组件证据、外部依赖状态与清理证据；全部 verified 才可 done。本次结构检查不代表功能测试通过。

### Log
- [2026-09-20] prepared (draft)
- [2026-09-21] 用户确认后写入；设计修订已承接，状态保持 draft。

---

## TASK-019: 补消息、去重与投递指标

- **Status**: draft
- **Priority**: P1
- **Depends**: TASK-008, TASK-015, TASK-017, TASK-018, TASK-021
- **Source**: 10-im-gateway.backend.design.md#4.2 指标目录, 10-im-gateway.backend.design.md#3.5 质量实现方案
- **Spec-Refs**: 
- **Acceptance-Refs**: B-119
- **Files**: `apps/im-gateway/src/muad_im_gateway/application/inbound.py`, `apps/im-gateway/src/muad_im_gateway/api/delivery.py`, `tests/gateway/test_message_metrics.py`
- **Estimate**: 15–60 分钟；超出先拆分

### Description

装配 im_messages_total、im_runtime_errors_total、im_stream_first_chunk_ms、im_dedupe_hits_total、im_background_delivery_total、im_runtime_request_latency_ms、im_stream_latency_ms、im_message_failures_total；性能阈值保持待实测。

### Checklist

- [ ] [B-119][integration] 修改生产代码前先按 生产入站/HTTP投递/真实SSE→metrics exporter 编写或扩展用例并记录 RED；关键断言：成功/失败/重复分支计数准确；首块和全流时延分开；无 secret/正文标签；同trace可关联。执行 argv：`["uv","run","pytest","-q","tests/gateway/test_message_metrics.py","-k","b119"]`。
- [ ] 实现或补齐：装配 im_messages_total、im_runtime_errors_total、im_stream_first_chunk_ms、im_dedupe_hits_total、im_background_delivery_total、im_runtime_request_latency_ms、im_stream_latency_ms、im_message_failures_total；性能阈值保持待实测。
- [ ] 执行上述契约命令，填写 Acceptance Evidence 的 RED/GREEN、每个关键断言位置、真实组件与清理记录；失败、skip 或外部阻塞保留未验证。所有代码改动有对应测试，函数≤50行，强类型与显式异常处理。

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| B-119 | integration | 生产入站/HTTP投递/真实SSE→metrics exporter | 成功/失败/重复分支计数准确；首块和全流时延分开；无 secret/正文标签；同trace可关联 | tests/gateway/test_message_metrics.py / B-119（planned） | `["uv","run","pytest","-q","tests/gateway/test_message_metrics.py","-k","b119"]` | planned |

### Acceptance Evidence
> planned。编码期填 RED/GREEN 命令与结果、断言路径/用例/位置、真实组件证据、外部依赖状态与清理证据；全部 verified 才可 done。本次结构检查不代表功能测试通过。

### Log
- [2026-09-20] prepared (draft)
- [2026-09-21] 用户确认后写入；设计修订已承接，状态保持 draft。

---

## TASK-020: 建立真实 WS 与官方 SDK 测试边界

- **Status**: draft
- **Priority**: P0
- **Depends**: 
- **Source**: 10-im-gateway.backend.design.md#2.5.2 功能验收场景, 10-im-gateway.backend.design.md#3.1 技术选型与关键决策
- **Spec-Refs**: 
- **Acceptance-Refs**: B-120
- **Files**: `tests/e2e/wecom_probe_app.py`, `tests/acceptance/im_gateway/test_wecom_boundary.py`
- **Estimate**: 15–60 分钟；超出先拆分

### Description

建立本地真实 WebSocket 协议探针，官方 SDK 与生产 Adapter 经真实 socket 收发认证/消息/流式回复，支持断线/握手拒绝/发送失败注入；替代外部第三方端点，不替代生产 Adapter/SDK。

### Checklist

- [ ] [B-120][integration] 修改生产代码前先按 官方 SDK→真实本地 WebSocket 服务→生产 WeComAdapter 编写或扩展用例并记录 RED；关键断言：协议帧可回读；连接/流式结束/主动发送均真实；禁止 FakeChannelAdapter/MockTransport；探针不宣称企业微信实网验收。执行 argv：`["uv","run","pytest","-q","tests/acceptance/im_gateway/test_wecom_boundary.py","-k","b120"]`。
- [ ] 实现或补齐：建立本地真实 WebSocket 协议探针，官方 SDK 与生产 Adapter 经真实 socket 收发认证/消息/流式回复，支持断线/握手拒绝/发送失败注入；替代外部第三方端点，不替代生产 Adapter/SDK。
- [ ] 执行上述契约命令，填写 Acceptance Evidence 的 RED/GREEN、每个关键断言位置、真实组件与清理记录；失败、skip 或外部阻塞保留未验证。所有代码改动有对应测试，函数≤50行，强类型与显式异常处理。

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| B-120 | integration | 官方 SDK→真实本地 WebSocket 服务→生产 WeComAdapter | 协议帧可回读；连接/流式结束/主动发送均真实；禁止 FakeChannelAdapter/MockTransport；探针不宣称企业微信实网验收 | tests/acceptance/im_gateway/test_wecom_boundary.py / B-120（planned） | `["uv","run","pytest","-q","tests/acceptance/im_gateway/test_wecom_boundary.py","-k","b120"]` | planned |

### Acceptance Evidence
> planned。编码期填 RED/GREEN 命令与结果、断言路径/用例/位置、真实组件证据、外部依赖状态与清理证据；全部 verified 才可 done。本次结构检查不代表功能测试通过。

### Log
- [2026-09-20] prepared (draft)
- [2026-09-21] 用户确认后写入；设计修订已承接，状态保持 draft。

---

## TASK-021: 建立 Gateway 多服务真实验收环境

- **Status**: draft
- **Priority**: P0
- **Depends**: TASK-020
- **Source**: 10-im-gateway.backend.design.md#2.5.2 功能验收场景, 10-im-gateway.backend.design.md#3.5 质量实现方案
- **Spec-Refs**: 
- **Acceptance-Refs**: B-121
- **Files**: `tests/acceptance/im_gateway/conftest.py`, `tests/acceptance/im_gateway/test_environment.py`, `tests/e2e/seed_im_gateway.py`
- **Estimate**: 15–60 分钟；超出先拆分

### Description

复用 Runtime/Console E2E 设施启动真实进程、两个 Runtime 实例、Worker、PostgreSQL、Redis 和 WS/模型探针；支持断线与进程重启；数据用 e2e-im-* 且 fixture finally 清理。缺依赖明确失败/阻塞，不用 skip 充当证据。

### Checklist

- [ ] [B-121][integration] 修改生产代码前先按 生产进程生命周期→真实HTTP/WS/PostgreSQL/Redis 编写或扩展用例并记录 RED；关键断言：进程健康可探测；模型经真实HTTP；故障注入可恢复；无残留DB数据、键或后台进程。执行 argv：`["uv","run","pytest","-q","tests/acceptance/im_gateway/test_environment.py","-k","b121"]`。
- [ ] 实现或补齐：复用 Runtime/Console E2E 设施启动真实进程、两个 Runtime 实例、Worker、PostgreSQL、Redis 和 WS/模型探针；支持断线与进程重启；数据用 e2e-im-* 且 fixture finally 清理。缺依赖明确失败/阻塞，不用 skip 充当证据。
- [ ] 执行上述契约命令，填写 Acceptance Evidence 的 RED/GREEN、每个关键断言位置、真实组件与清理记录；失败、skip 或外部阻塞保留未验证。所有代码改动有对应测试，函数≤50行，强类型与显式异常处理。

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| B-121 | integration | 生产进程生命周期→真实HTTP/WS/PostgreSQL/Redis | 进程健康可探测；模型经真实HTTP；故障注入可恢复；无残留DB数据、键或后台进程 | tests/acceptance/im_gateway/test_environment.py / B-121（planned） | `["uv","run","pytest","-q","tests/acceptance/im_gateway/test_environment.py","-k","b121"]` | planned |

### Acceptance Evidence
> planned。编码期填 RED/GREEN 命令与结果、断言路径/用例/位置、真实组件证据、外部依赖状态与清理证据；全部 verified 才可 done。本次结构检查不代表功能测试通过。

### Log
- [2026-09-20] prepared (draft)
- [2026-09-21] 用户确认后写入；设计修订已承接，状态保持 draft。

---

## TASK-022: 验收多 Bot 路由与任意 Runtime 实例

- **Status**: draft
- **Priority**: P0
- **Depends**: TASK-005, TASK-006, TASK-009, TASK-013, TASK-016, TASK-021
- **Source**: 10-im-gateway.backend.design.md#2.5.2 功能验收场景, 10-im-gateway.backend.design.md#3.1 技术选型与关键决策, 10-im-gateway.backend.design.md#Spec Compliance Matrix
- **Spec-Refs**: harness-arch#RULE-arch-001, harness-im#RULE-im-001
- **Acceptance-Refs**: B-122, S-01, RULE-01, RULE-04, RISK-01, RULE-arch-001, RULE-im-001
- **Files**: `tests/acceptance/im_gateway/test_routing.py`, `tests/architecture/test_im_gateway_boundaries.py`
- **Estimate**: 15–60 分钟；超出先拆分

### Description

两个 bot 指向同一 Agent，以真实消息进入 Gateway，经正常服务入口分别在两个 Runtime 实例执行；验证 SDK 类型隔离、四部署单元及无 bot/Agent→Pod 映射。

### Checklist

- [ ] [B-122][E2E] 修改生产代码前先按 官方SDK/WeComAdapter→Gateway→Console/PG→真实双Runtime HTTP 编写或扩展用例并记录 RED；关键断言：两bot同逻辑Agent，实例可替换；无路由绑Pod；Runtime/Worker不导入SDK；禁用bot无Run。执行 argv：`["uv","run","pytest","-q","tests/acceptance/im_gateway/test_routing.py","-k","b122"]`。
- [ ] [S-01][E2E] 修改生产代码前先按 官方SDK/WeComAdapter→Gateway→真实Console/PG与双Runtime 编写或扩展用例并记录 RED；关键断言：两个bot同一Agent；可进入不同Runtime；无Pod绑定。执行 argv：`["uv","run","pytest","-q","tests/acceptance/im_gateway/test_routing.py","-k","s01"]`。
- [ ] 实现或补齐：两个 bot 指向同一 Agent，以真实消息进入 Gateway，经正常服务入口分别在两个 Runtime 实例执行；验证 SDK 类型隔离、四部署单元及无 bot/Agent→Pod 映射。
- [ ] [RULE-arch-001][E2E] verifier_ref=harness-arch#RULE-arch-001；原 verifier 输入 argv=`["uv","run","pytest","-q","tests/architecture"]`；补充真实边界 官方SDK/WeComAdapter→Gateway→Console/PG→真实双Runtime HTTP；原 Spec verifier 真实边界，断言 两bot同逻辑Agent，实例可替换；无路由绑Pod；Runtime/Worker不导入SDK；禁用bot无Run；原 verifier 全部通过，联合验收 argv=`["bash","-lc","uv run pytest -q tests/acceptance/im_gateway/test_routing.py && uv run pytest -q tests/architecture"]`。
- [ ] [RULE-im-001][E2E] verifier_ref=harness-im#RULE-im-001；原 verifier 输入 argv=`["uv","run","pytest","-q","tests/console_channel","tests/gateway"]`；补充真实边界 官方SDK/WeComAdapter→Gateway→Console/PG→真实双Runtime HTTP；原 Spec verifier 真实边界，断言 两bot同逻辑Agent，实例可替换；无路由绑Pod；Runtime/Worker不导入SDK；禁用bot无Run；原 verifier 全部通过，联合验收 argv=`["bash","-lc","uv run pytest -q tests/acceptance/im_gateway/test_routing.py && uv run pytest -q tests/console_channel tests/gateway"]`。
- [ ] [RULE-01][E2E] 作为唯一最终负责人，沿 官方SDK/WeComAdapter→Gateway→Console/PG→真实双Runtime HTTP 验证 四部署单元、Runtime/Worker无状态与任意实例；联合映射 S-01；命令 `["uv","run","pytest","-q","tests/acceptance/im_gateway/test_routing.py","-k","b122"]`，不得以任务标题或静态声明代替行为证据。
- [ ] [RULE-04][E2E] 作为唯一最终负责人，沿 官方SDK/WeComAdapter→Gateway→Console/PG→真实双Runtime HTTP 验证 Agent 0..N bot，bot只路由一个Agent，无Pod绑定；联合映射 S-01 / E-01；命令 `["uv","run","pytest","-q","tests/acceptance/im_gateway/test_routing.py","-k","b122"]`，不得以任务标题或静态声明代替行为证据。
- [ ] [RISK-01][E2E] 作为唯一最终负责人，沿 官方SDK/WeComAdapter→Gateway→Console/PG→真实双Runtime HTTP 验证 SDK类型隔离，iter_events唯一入口；联合映射 S-01；命令 `["uv","run","pytest","-q","tests/acceptance/im_gateway/test_routing.py","-k","b122"]`，不得以任务标题或静态声明代替行为证据。
- [ ] 执行上述契约命令，填写 Acceptance Evidence 的 RED/GREEN、每个关键断言位置、真实组件与清理记录；失败、skip 或外部阻塞保留未验证。所有代码改动有对应测试，函数≤50行，强类型与显式异常处理。

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| B-122 | E2E | 官方SDK/WeComAdapter→Gateway→Console/PG→真实双Runtime HTTP | 两bot同逻辑Agent，实例可替换；无路由绑Pod；Runtime/Worker不导入SDK；禁用bot无Run | tests/acceptance/im_gateway/test_routing.py / B-122（planned） | `["uv","run","pytest","-q","tests/acceptance/im_gateway/test_routing.py","-k","b122"]` | planned |
| S-01 | E2E | 官方SDK/WeComAdapter→Gateway→真实Console/PG与双Runtime | 两个bot同一Agent；可进入不同Runtime；无Pod绑定 | tests/acceptance/im_gateway/test_routing.py / S-01（planned） | `["uv","run","pytest","-q","tests/acceptance/im_gateway/test_routing.py","-k","s01"]` | planned |
| RULE-01 | E2E | 官方SDK/WeComAdapter→Gateway→Console/PG→真实双Runtime HTTP | 四部署单元、Runtime/Worker无状态与任意实例；联合映射 S-01 | tests/acceptance/im_gateway/test_routing.py / RULE-01（planned） | `["uv","run","pytest","-q","tests/acceptance/im_gateway/test_routing.py","-k","b122"]` | planned |
| RULE-04 | E2E | 官方SDK/WeComAdapter→Gateway→Console/PG→真实双Runtime HTTP | Agent 0..N bot，bot只路由一个Agent，无Pod绑定；联合映射 S-01 / E-01 | tests/acceptance/im_gateway/test_routing.py / RULE-04（planned） | `["uv","run","pytest","-q","tests/acceptance/im_gateway/test_routing.py","-k","b122"]` | planned |
| RISK-01 | E2E | 官方SDK/WeComAdapter→Gateway→Console/PG→真实双Runtime HTTP | SDK类型隔离，iter_events唯一入口；联合映射 S-01 | tests/acceptance/im_gateway/test_routing.py / RISK-01（planned） | `["uv","run","pytest","-q","tests/acceptance/im_gateway/test_routing.py","-k","b122"]` | planned |
| RULE-arch-001 | E2E | 官方SDK/WeComAdapter→Gateway→Console/PG→真实双Runtime HTTP；原 Spec verifier 真实边界 | 两bot同逻辑Agent，实例可替换；无路由绑Pod；Runtime/Worker不导入SDK；禁用bot无Run；原 verifier 全部通过 | tests/acceptance/im_gateway/test_routing.py + 原 verifier / RULE-arch-001（planned） | `["bash","-lc","uv run pytest -q tests/acceptance/im_gateway/test_routing.py && uv run pytest -q tests/architecture"]` | planned |
| RULE-im-001 | E2E | 官方SDK/WeComAdapter→Gateway→Console/PG→真实双Runtime HTTP；原 Spec verifier 真实边界 | 两bot同逻辑Agent，实例可替换；无路由绑Pod；Runtime/Worker不导入SDK；禁用bot无Run；原 verifier 全部通过 | tests/acceptance/im_gateway/test_routing.py + 原 verifier / RULE-im-001（planned） | `["bash","-lc","uv run pytest -q tests/acceptance/im_gateway/test_routing.py && uv run pytest -q tests/console_channel tests/gateway"]` | planned |

### Acceptance Evidence
> planned。编码期填 RED/GREEN 命令与结果、断言路径/用例/位置、真实组件证据、外部依赖状态与清理证据；全部 verified 才可 done。本次结构检查不代表功能测试通过。

### Log
- [2026-09-20] prepared (draft)
- [2026-09-21] 用户确认后写入；设计修订已承接，状态保持 draft。

---

## TASK-023: 验收绑定链路与双语 API 封套

- **Status**: draft
- **Priority**: P0
- **Depends**: TASK-002, TASK-003, TASK-010, TASK-021
- **Source**: 10-im-gateway.backend.design.md#2.5.2 功能验收场景, 10-im-gateway.backend.design.md#API-03 执行绑定, 10-im-gateway.backend.design.md#3.4 接口设计, 10-im-gateway.backend.design.md#Spec Compliance Matrix
- **Spec-Refs**: harness-api#RULE-api-001
- **Acceptance-Refs**: B-123, S-02, E-02, RULE-02, RULE-api-001
- **Files**: `tests/acceptance/im_gateway/test_binding.py`, `tests/e2e/test_gateway_bind_e2e.py`
- **Estimate**: 15–60 分钟；超出先拆分

### Description

扩展已有只覆盖 ConsoleClient→Console→PG 的 S-02，纳入真实 Gateway 命令处理与最终回复；覆盖无效/过期/已用绑定码以及统一 envelope/catalog、页码边界。

### Checklist

- [ ] [B-123][E2E] 修改生产代码前先按 真实WS→Gateway命令→Console bind HTTP→PostgreSQL→SDK回复 编写或扩展用例并记录 RED；关键断言：绑定成功与身份记录一致；错误HTTP/code/msg从catalog映射；trace/request/timestamp完整；无授权扩张。执行 argv：`["uv","run","pytest","-q","tests/acceptance/im_gateway/test_binding.py","-k","b123"]`。
- [ ] [S-02][E2E] 修改生产代码前先按 Gateway完整/bind命令→真实Console HTTP→PostgreSQL→最终回复 编写或扩展用例并记录 RED；关键断言：有效码绑定成功、回复已验证；身份持久；不隐式授予Agent权限。执行 argv：`["uv","run","pytest","-q","tests/acceptance/im_gateway/test_binding.py","-k","s02"]`。
- [ ] [E-02][integration] 修改生产代码前先按 真实Console bind HTTP→PostgreSQL bind_code/identity 编写或扩展用例并记录 RED；关键断言：无效/已用BIND_CODE_INVALID；过期BIND_CODE_EXPIRED；事务无副作用。执行 argv：`["uv","run","pytest","-q","tests/acceptance/im_gateway/test_binding.py","-k","e02"]`。
- [ ] 实现或补齐：扩展已有只覆盖 ConsoleClient→Console→PG 的 S-02，纳入真实 Gateway 命令处理与最终回复；覆盖无效/过期/已用绑定码以及统一 envelope/catalog、页码边界。
- [ ] [RULE-api-001][E2E] verifier_ref=harness-api#RULE-api-001；原 verifier 输入 argv=`["uv","run","pytest","-q","tests/test_api_i18n.py","tests/test_error_catalog.py","tests/acceptance/test_foundation_api_envelope.py"]`；补充真实边界 真实WS→Gateway命令→Console bind HTTP→PostgreSQL→SDK回复；原 Spec verifier 真实边界，断言 绑定成功与身份记录一致；错误HTTP/code/msg从catalog映射；trace/request/timestamp完整；无授权扩张；原 verifier 全部通过，联合验收 argv=`["bash","-lc","uv run pytest -q tests/acceptance/im_gateway/test_binding.py && uv run pytest -q tests/test_api_i18n.py tests/test_error_catalog.py tests/acceptance/test_foundation_api_envelope.py"]`。
- [ ] [RULE-02][E2E] 作为唯一最终负责人，沿 真实WS→Gateway命令→Console bind HTTP→PostgreSQL→SDK回复 验证 统一封套、catalog错误码及分页；联合映射 S-02 / E-02；命令 `["uv","run","pytest","-q","tests/acceptance/im_gateway/test_binding.py","-k","b123"]`，不得以任务标题或静态声明代替行为证据。
- [ ] 执行上述契约命令，填写 Acceptance Evidence 的 RED/GREEN、每个关键断言位置、真实组件与清理记录；失败、skip 或外部阻塞保留未验证。所有代码改动有对应测试，函数≤50行，强类型与显式异常处理。

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| B-123 | E2E | 真实WS→Gateway命令→Console bind HTTP→PostgreSQL→SDK回复 | 绑定成功与身份记录一致；错误HTTP/code/msg从catalog映射；trace/request/timestamp完整；无授权扩张 | tests/acceptance/im_gateway/test_binding.py / B-123（planned） | `["uv","run","pytest","-q","tests/acceptance/im_gateway/test_binding.py","-k","b123"]` | planned |
| S-02 | E2E | Gateway完整/bind命令→真实Console HTTP→PostgreSQL→最终回复 | 有效码绑定成功、回复已验证；身份持久；不隐式授予Agent权限 | tests/acceptance/im_gateway/test_binding.py / S-02（planned） | `["uv","run","pytest","-q","tests/acceptance/im_gateway/test_binding.py","-k","s02"]` | planned |
| E-02 | integration | 真实Console bind HTTP→PostgreSQL bind_code/identity | 无效/已用BIND_CODE_INVALID；过期BIND_CODE_EXPIRED；事务无副作用 | tests/acceptance/im_gateway/test_binding.py / E-02（planned） | `["uv","run","pytest","-q","tests/acceptance/im_gateway/test_binding.py","-k","e02"]` | planned |
| RULE-02 | E2E | 真实WS→Gateway命令→Console bind HTTP→PostgreSQL→SDK回复 | 统一封套、catalog错误码及分页；联合映射 S-02 / E-02 | tests/acceptance/im_gateway/test_binding.py / RULE-02（planned） | `["uv","run","pytest","-q","tests/acceptance/im_gateway/test_binding.py","-k","b123"]` | planned |
| RULE-api-001 | E2E | 真实WS→Gateway命令→Console bind HTTP→PostgreSQL→SDK回复；原 Spec verifier 真实边界 | 绑定成功与身份记录一致；错误HTTP/code/msg从catalog映射；trace/request/timestamp完整；无授权扩张；原 verifier 全部通过 | tests/acceptance/im_gateway/test_binding.py + 原 verifier / RULE-api-001（planned） | `["bash","-lc","uv run pytest -q tests/acceptance/im_gateway/test_binding.py && uv run pytest -q tests/test_api_i18n.py tests/test_error_catalog.py tests/acceptance/test_foundation_api_envelope.py"]` | planned |

### Acceptance Evidence
> planned。编码期填 RED/GREEN 命令与结果、断言路径/用例/位置、真实组件证据、外部依赖状态与清理证据；全部 verified 才可 done。本次结构检查不代表功能测试通过。

### Log
- [2026-09-20] prepared (draft)
- [2026-09-21] 用户确认后写入；设计修订已承接，状态保持 draft。

---

## TASK-024: 验收 Effective Capability 与命令权限

- **Status**: draft
- **Priority**: P0
- **Depends**: TASK-004, TASK-009, TASK-011, TASK-021
- **Source**: 10-im-gateway.backend.design.md#API-04 查询可用 Skills, 10-im-gateway.backend.design.md#API-02 解析消息路由, 10-im-gateway.backend.design.md#2.5.1 业务规则与约束, 10-im-gateway.backend.design.md#Spec Compliance Matrix
- **Spec-Refs**: harness-auth#RULE-auth-001
- **Acceptance-Refs**: B-124, RULE-05, RULE-auth-001
- **Files**: `tests/acceptance/im_gateway/test_authorization.py`
- **Estimate**: 15–60 分钟；超出先拆分

### Description

用真实 Grant/Binding/SELECTED 数据验证 /skills 目录和普通消息授权；覆盖 Agent/资源 enabled/is_deleted，以及 /new 不变更授权/记忆。

### Checklist

- [ ] [B-124][E2E] 修改生产代码前先按 WS命令→Gateway→Console授权HTTP/PG→Runtime Prompt/ToolRegistry 编写或扩展用例并记录 RED；关键断言：未授权资源名称/描述/Prompt/Tool/SkillCatalog均不可见；没有绑定启停/授权到期/三元授权；新会话不改变绑定。执行 argv：`["uv","run","pytest","-q","tests/acceptance/im_gateway/test_authorization.py","-k","b124"]`。
- [ ] 实现或补齐：用真实 Grant/Binding/SELECTED 数据验证 /skills 目录和普通消息授权；覆盖 Agent/资源 enabled/is_deleted，以及 /new 不变更授权/记忆。
- [ ] [RULE-auth-001][E2E] verifier_ref=harness-auth#RULE-auth-001；原 verifier 输入 argv=`["bash","-lc","uv run pytest -q tests/console_platform/test_user_side_relations.py -k s04 && uv run pytest -q tests -k schema_parity"]`；补充真实边界 WS命令→Gateway→Console授权HTTP/PG→Runtime Prompt/ToolRegistry；原 Spec verifier 真实边界，断言 未授权资源名称/描述/Prompt/Tool/SkillCatalog均不可见；没有绑定启停/授权到期/三元授权；新会话不改变绑定；原 verifier 全部通过，联合验收 argv=`["bash","-lc","uv run pytest -q tests/acceptance/im_gateway/test_authorization.py && uv run pytest -q tests/console_platform/test_user_side_relations.py -k s04 && uv run pytest -q tests -k schema_parity"]`。
- [ ] [RULE-05][E2E] 作为唯一最终负责人，沿 WS命令→Gateway→Console授权HTTP/PG→Runtime Prompt/ToolRegistry 验证 三层授权与Effective Capability；补充B-124避免仅RUN_BUSY冒充授权验证；联合映射 S-03 / E-04 / B-124；命令 `["uv","run","pytest","-q","tests/acceptance/im_gateway/test_authorization.py","-k","b124"]`，不得以任务标题或静态声明代替行为证据。
- [ ] 执行上述契约命令，填写 Acceptance Evidence 的 RED/GREEN、每个关键断言位置、真实组件与清理记录；失败、skip 或外部阻塞保留未验证。所有代码改动有对应测试，函数≤50行，强类型与显式异常处理。

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| B-124 | E2E | WS命令→Gateway→Console授权HTTP/PG→Runtime Prompt/ToolRegistry | 未授权资源名称/描述/Prompt/Tool/SkillCatalog均不可见；没有绑定启停/授权到期/三元授权；新会话不改变绑定 | tests/acceptance/im_gateway/test_authorization.py / B-124（planned） | `["uv","run","pytest","-q","tests/acceptance/im_gateway/test_authorization.py","-k","b124"]` | planned |
| RULE-05 | E2E | WS命令→Gateway→Console授权HTTP/PG→Runtime Prompt/ToolRegistry | 三层授权与Effective Capability；补充B-124避免仅RUN_BUSY冒充授权验证；联合映射 S-03 / E-04 / B-124 | tests/acceptance/im_gateway/test_authorization.py / RULE-05（planned） | `["uv","run","pytest","-q","tests/acceptance/im_gateway/test_authorization.py","-k","b124"]` | planned |
| RULE-auth-001 | E2E | WS命令→Gateway→Console授权HTTP/PG→Runtime Prompt/ToolRegistry；原 Spec verifier 真实边界 | 未授权资源名称/描述/Prompt/Tool/SkillCatalog均不可见；没有绑定启停/授权到期/三元授权；新会话不改变绑定；原 verifier 全部通过 | tests/acceptance/im_gateway/test_authorization.py + 原 verifier / RULE-auth-001（planned） | `["bash","-lc","uv run pytest -q tests/acceptance/im_gateway/test_authorization.py && uv run pytest -q tests/console_platform/test_user_side_relations.py -k s04 && uv run pytest -q tests -k schema_parity"]` | planned |

### Acceptance Evidence
> planned。编码期填 RED/GREEN 命令与结果、断言路径/用例/位置、真实组件证据、外部依赖状态与清理证据；全部 verified 才可 done。本次结构检查不代表功能测试通过。

### Log
- [2026-09-20] prepared (draft)
- [2026-09-21] 用户确认后写入；设计修订已承接，状态保持 draft。

---

## TASK-025: 验收流式回复、Resume、取消与 Snapshot

- **Status**: draft
- **Priority**: P0
- **Depends**: TASK-012, TASK-014, TASK-015, TASK-016, TASK-021
- **Source**: 10-im-gateway.backend.design.md#2.5.2 功能验收场景, 10-im-gateway.backend.design.md#3.4.1 Runtime SSE 事件处理（FEAT-03）, 10-im-gateway.backend.design.md#Spec Compliance Matrix
- **Spec-Refs**: harness-snapshot#RULE-snapshot-001
- **Acceptance-Refs**: B-125, S-03, E-03, E-04, RULE-06, RULE-10, RULE-snapshot-001
- **Files**: `tests/acceptance/im_gateway/test_runtime_stream.py`, `tests/acceptance/im_gateway/test_recovery.py`
- **Estimate**: 15–60 分钟；超出先拆分
- **External-Depends**: EXT-08（真实 Runtime 契约与对应验收 Evidence）

### Description

验证真实 Run 流、普通消息自动恢复、忙碌拒绝、停止与断流回收；配置变更后当前快照不变、新 Run 使用新配置；Reaper 终态 CAS 无重复副作用。原 E-03/E-04 保留 integration 契约，另用补充 E2E 覆盖跨服务链路。

### Checklist

- [ ] [B-125][E2E] 修改生产代码前先按 真实WS→Gateway→Runtime HTTP/SSE→PostgreSQL Snapshot/Reaper→SDK回复 编写或扩展用例并记录 RED；关键断言：seq单调；resumed=true沿用原Run；RUN_BUSY无新Run；断流文案正确且Reaper FAILED/RUN_ABANDONED；Snapshot冻结与终态CAS。执行 argv：`["uv","run","pytest","-q","tests/acceptance/im_gateway/test_runtime_stream.py","-k","b125"]`。
- [ ] [S-03][E2E] 修改生产代码前先按 真实WeCom协议WS→Gateway→Runtime SSE/PG→官方SDK出站 编写或扩展用例并记录 RED；关键断言：授权消息创建Run；seq单调；run.completed正确收尾；无业务API mock。执行 argv：`["uv","run","pytest","-q","tests/acceptance/im_gateway/test_runtime_stream.py","-k","s03"]`。
- [ ] [E-03][integration] 修改生产代码前先按 Gateway→真实Runtime SSE断线→Reaper/PostgreSQL 编写或扩展用例并记录 RED；关键断言：未收终态即断开显示重发提示；Reaper回收FAILED/RUN_ABANDONED；终态CAS。执行 argv：`["uv","run","pytest","-q","tests/acceptance/im_gateway/test_recovery.py","-k","e03"]`。
- [ ] [E-04][integration] 修改生产代码前先按 真实Runtime/PG→Gateway HTTP错误处理 编写或扩展用例并记录 RED；关键断言：CREATED/RUNNING冲突返回RUN_BUSY；无新Run且原状态不变；提示可/stop。执行 argv：`["uv","run","pytest","-q","tests/acceptance/im_gateway/test_runtime_stream.py","-k","e04"]`。
- [ ] 实现或补齐：验证真实 Run 流、普通消息自动恢复、忙碌拒绝、停止与断流回收；配置变更后当前快照不变、新 Run 使用新配置；Reaper 终态 CAS 无重复副作用。原 E-03/E-04 保留 integration 契约，另用补充 E2E 覆盖跨服务链路。
- [ ] [RULE-snapshot-001][E2E] verifier_ref=harness-snapshot#RULE-snapshot-001；原 verifier 输入 argv=`["bash","-lc","uv run pytest -q tests/agent_runtime -k \"executor or resolve\""]`；补充真实边界 真实WS→Gateway→Runtime HTTP/SSE→PostgreSQL Snapshot/Reaper→SDK回复；原 Spec verifier 真实边界，断言 seq单调；resumed=true沿用原Run；RUN_BUSY无新Run；断流文案正确且Reaper FAILED/RUN_ABANDONED；Snapshot冻结与终态CAS；原 verifier 全部通过，联合验收 argv=`["bash","-lc","uv run pytest -q tests/acceptance/im_gateway/test_runtime_stream.py && uv run pytest -q tests/agent_runtime -k \"executor or resolve\""]`。
- [ ] [RULE-06][E2E] 作为唯一最终负责人，沿 真实WS→Gateway→Runtime HTTP/SSE→PostgreSQL Snapshot/Reaper→SDK回复 验证 Snapshot冻结与终态CAS；联合映射 S-03 / E-03；命令 `["uv","run","pytest","-q","tests/acceptance/im_gateway/test_runtime_stream.py","-k","b125"]`，不得以任务标题或静态声明代替行为证据。
- [ ] [RULE-10][E2E] 作为唯一最终负责人，沿 真实WS→Gateway→Runtime HTTP/SSE→PostgreSQL Snapshot/Reaper→SDK回复 验证 未绑定正常分支、自动resume、并发和取消错误语义；联合映射 S-06 / E-04 / E-05 / B-125；命令 `["uv","run","pytest","-q","tests/acceptance/im_gateway/test_runtime_stream.py","-k","b125"]`，不得以任务标题或静态声明代替行为证据。
- [ ] 执行上述契约命令，填写 Acceptance Evidence 的 RED/GREEN、每个关键断言位置、真实组件与清理记录；失败、skip 或外部阻塞保留未验证。所有代码改动有对应测试，函数≤50行，强类型与显式异常处理。

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| B-125 | E2E | 真实WS→Gateway→Runtime HTTP/SSE→PostgreSQL Snapshot/Reaper→SDK回复 | seq单调；resumed=true沿用原Run；RUN_BUSY无新Run；断流文案正确且Reaper FAILED/RUN_ABANDONED；Snapshot冻结与终态CAS | tests/acceptance/im_gateway/test_runtime_stream.py / B-125（planned） | `["uv","run","pytest","-q","tests/acceptance/im_gateway/test_runtime_stream.py","-k","b125"]` | planned |
| S-03 | E2E | 真实WeCom协议WS→Gateway→Runtime SSE/PG→官方SDK出站 | 授权消息创建Run；seq单调；run.completed正确收尾；无业务API mock | tests/acceptance/im_gateway/test_runtime_stream.py / S-03（planned） | `["uv","run","pytest","-q","tests/acceptance/im_gateway/test_runtime_stream.py","-k","s03"]` | planned |
| E-03 | integration | Gateway→真实Runtime SSE断线→Reaper/PostgreSQL | 未收终态即断开显示重发提示；Reaper回收FAILED/RUN_ABANDONED；终态CAS | tests/acceptance/im_gateway/test_recovery.py / E-03（planned） | `["uv","run","pytest","-q","tests/acceptance/im_gateway/test_recovery.py","-k","e03"]` | planned |
| E-04 | integration | 真实Runtime/PG→Gateway HTTP错误处理 | CREATED/RUNNING冲突返回RUN_BUSY；无新Run且原状态不变；提示可/stop | tests/acceptance/im_gateway/test_runtime_stream.py / E-04（planned） | `["uv","run","pytest","-q","tests/acceptance/im_gateway/test_runtime_stream.py","-k","e04"]` | planned |
| RULE-06 | E2E | 真实WS→Gateway→Runtime HTTP/SSE→PostgreSQL Snapshot/Reaper→SDK回复 | Snapshot冻结与终态CAS；联合映射 S-03 / E-03 | tests/acceptance/im_gateway/test_runtime_stream.py / RULE-06（planned） | `["uv","run","pytest","-q","tests/acceptance/im_gateway/test_runtime_stream.py","-k","b125"]` | planned |
| RULE-10 | E2E | 真实WS→Gateway→Runtime HTTP/SSE→PostgreSQL Snapshot/Reaper→SDK回复 | 未绑定正常分支、自动resume、并发和取消错误语义；联合映射 S-06 / E-04 / E-05 / B-125 | tests/acceptance/im_gateway/test_runtime_stream.py / RULE-10（planned） | `["uv","run","pytest","-q","tests/acceptance/im_gateway/test_runtime_stream.py","-k","b125"]` | planned |
| RULE-snapshot-001 | E2E | 真实WS→Gateway→Runtime HTTP/SSE→PostgreSQL Snapshot/Reaper→SDK回复；原 Spec verifier 真实边界 | seq单调；resumed=true沿用原Run；RUN_BUSY无新Run；断流文案正确且Reaper FAILED/RUN_ABANDONED；Snapshot冻结与终态CAS；原 verifier 全部通过 | tests/acceptance/im_gateway/test_runtime_stream.py + 原 verifier / RULE-snapshot-001（planned） | `["bash","-lc","uv run pytest -q tests/acceptance/im_gateway/test_runtime_stream.py && uv run pytest -q tests/agent_runtime -k \"executor or resolve\""]` | planned |

### Acceptance Evidence
> planned。编码期填 RED/GREEN 命令与结果、断言路径/用例/位置、真实组件证据、外部依赖状态与清理证据；全部 verified 才可 done。本次结构检查不代表功能测试通过。

### Log
- [2026-09-20] prepared (draft)
- [2026-09-21] 用户确认后写入；设计修订已承接，状态保持 draft。

---

## TASK-026: 验收绑定和 Run 的端到端幂等

- **Status**: draft
- **Priority**: P0
- **Depends**: TASK-003, TASK-010, TASK-013, TASK-021
- **Source**: 10-im-gateway.backend.design.md#API-03 执行绑定, 10-im-gateway.backend.design.md#API-06 Runtime Run 桥接, 10-im-gateway.backend.design.md#Spec Compliance Matrix
- **Spec-Refs**: harness-api#RULE-api-002
- **Acceptance-Refs**: B-126, RULE-api-002
- **Files**: `tests/acceptance/im_gateway/test_idempotency.py`
- **Estimate**: 15–60 分钟；超出先拆分
- **External-Depends**: EXT-08（真实 Runtime 契约与对应验收 Evidence）

### Description

补新增必选 Rule 的真实验收；同消息同payload、并发与进程重启后回读持久首次结果；同key不同指纹不执行副作用。明确期望 409 COMMON_CONFLICT；当前 Runtime IDEMPOTENCY_MISMATCH 是外部实现差异，启动前要求 Owner 对齐并给出证据。另覆盖 /new 同命令重放不创建第二会话；不接受两个错误码任选其一。

### Checklist

- [ ] [B-126][E2E] 修改生产代码前先按 Gateway HTTP→Console/Runtime→真实PostgreSQL幂等表/partial unique→可观测副作用 编写或扩展用例并记录 RED；关键断言：稳定Idempotency-Key；一次绑定/Run/新会话；首次结果200重放；租户和endpoint隔离；异指纹409 COMMON_CONFLICT。执行 argv：`["uv","run","pytest","-q","tests/acceptance/im_gateway/test_idempotency.py","-k","b126"]`。
- [ ] 实现或补齐：补新增必选 Rule 的真实验收；同消息同payload、并发与进程重启后回读持久首次结果；同key不同指纹不执行副作用。明确期望 409 COMMON_CONFLICT；当前 Runtime IDEMPOTENCY_MISMATCH 是外部实现差异，启动前要求 Owner 对齐并给出证据。另覆盖 /new 同命令重放不创建第二会话；不接受两个错误码任选其一。
- [ ] [RULE-api-002][E2E] verifier_ref=harness-api#RULE-api-002；原 verifier 输入 argv=`["uv","run","pytest","-q","tests/console_skill/test_import_idempotency.py"]`；补充真实边界 Gateway HTTP→Console/Runtime→真实PostgreSQL幂等表/partial unique→可观测副作用；原 Spec verifier 真实边界，断言 稳定Idempotency-Key；一次绑定/Run/新会话；首次结果200重放；租户和endpoint隔离；异指纹409 COMMON_CONFLICT；原 verifier 全部通过，联合验收 argv=`["bash","-lc","uv run pytest -q tests/acceptance/im_gateway/test_idempotency.py && uv run pytest -q tests/console_skill/test_import_idempotency.py"]`。
- [ ] 执行上述契约命令，填写 Acceptance Evidence 的 RED/GREEN、每个关键断言位置、真实组件与清理记录；失败、skip 或外部阻塞保留未验证。所有代码改动有对应测试，函数≤50行，强类型与显式异常处理。

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| B-126 | E2E | Gateway HTTP→Console/Runtime→真实PostgreSQL幂等表/partial unique→可观测副作用 | 稳定Idempotency-Key；一次绑定/Run/新会话；首次结果200重放；租户和endpoint隔离；异指纹409 COMMON_CONFLICT | tests/acceptance/im_gateway/test_idempotency.py / B-126（planned） | `["uv","run","pytest","-q","tests/acceptance/im_gateway/test_idempotency.py","-k","b126"]` | planned |
| RULE-api-002 | E2E | Gateway HTTP→Console/Runtime→真实PostgreSQL幂等表/partial unique→可观测副作用；原 Spec verifier 真实边界 | 稳定Idempotency-Key；一次绑定/Run/新会话；首次结果200重放；租户和endpoint隔离；异指纹409 COMMON_CONFLICT；原 verifier 全部通过 | tests/acceptance/im_gateway/test_idempotency.py + 原 verifier / RULE-api-002（planned） | `["bash","-lc","uv run pytest -q tests/acceptance/im_gateway/test_idempotency.py && uv run pytest -q tests/console_skill/test_import_idempotency.py"]` | planned |

### Acceptance Evidence
> planned。编码期填 RED/GREEN 命令与结果、断言路径/用例/位置、真实组件证据、外部依赖状态与清理证据；全部 verified 才可 done。本次结构检查不代表功能测试通过。

### Log
- [2026-09-20] prepared (draft)
- [2026-09-21] 用户确认后写入；设计修订已承接，状态保持 draft。

---

## TASK-027: 验收 Worker 主动投递及 Redis 故障

- **Status**: draft
- **Priority**: P0
- **Depends**: TASK-008, TASK-017, TASK-021
- **Source**: 10-im-gateway.backend.design.md#2.5.2 功能验收场景, 10-im-gateway.backend.design.md#API-05 主动投递, 10-im-gateway.backend.design.md#3.2.3 入站去重, 10-im-gateway.backend.design.md#5. 风险与依赖
- **Spec-Refs**: 
- **Acceptance-Refs**: B-127, S-04, E-06, RULE-09, RISK-02
- **Files**: `tests/acceptance/im_gateway/test_delivery.py`, `tests/acceptance/im_gateway/test_redis_degradation.py`
- **Estimate**: 15–60 分钟；超出先拆分
- **External-Depends**: EXT-09-020, EXT-09-021（最终 S-04 同时对照 EXT-09-043）

### Description

复用 EXT-09-020/021/043 可靠投递实现与证据，以本模块 S-04/E-06 验证 Worker→Gateway→SDK 完整链路；入站和投递 Redis 故障均继续 at-least-once，发送失败不误标成功。

### Checklist

- [ ] [B-127][E2E] 修改生产代码前先按 真实Worker/PG→Gateway HTTP→真实Redis→官方SDK/WS接收端 编写或扩展用例并记录 RED；关键断言：路由准确；首次发送/重复200不重发；TTL7d；失败可重试，Worker最多5次后FAILED；故障/不确定发送允许重复但不吞业务事实。执行 argv：`["uv","run","pytest","-q","tests/acceptance/im_gateway/test_delivery.py","-k","b127"]`。
- [ ] [S-04][E2E] 修改生产代码前先按 真实Worker→Gateway HTTP→Redis→官方SDK/真实WS接收 编写或扩展用例并记录 RED；关键断言：delivery_key固定；按route推送最终结果；重放200/deduplicated=true且不重发。执行 argv：`["uv","run","pytest","-q","tests/acceptance/im_gateway/test_delivery.py","-k","s04"]`。
- [ ] [E-06][integration] 修改生产代码前先按 Gateway入站/投递→真实Redis连接故障→Runtime/WS 编写或扩展用例并记录 RED；关键断言：两条路径均at-least-once继续；故障时允许重复但不吞业务事实；恢复后去重恢复。执行 argv：`["uv","run","pytest","-q","tests/acceptance/im_gateway/test_redis_degradation.py","-k","e06"]`。
- [ ] 实现或补齐：复用 EXT-09-020/021/043 可靠投递实现与证据，以本模块 S-04/E-06 验证 Worker→Gateway→SDK 完整链路；入站和投递 Redis 故障均继续 at-least-once，发送失败不误标成功。
- [ ] [RULE-09][E2E] 作为唯一最终负责人，沿 真实Worker/PG→Gateway HTTP→真实Redis→官方SDK/WS接收端 验证 投递key、7d去重、200重放与降级；联合映射 S-04 / E-06；命令 `["uv","run","pytest","-q","tests/acceptance/im_gateway/test_delivery.py","-k","b127"]`，不得以任务标题或静态声明代替行为证据。
- [ ] [RISK-02][E2E] 作为唯一最终负责人，沿 真实Worker/PG→Gateway HTTP→真实Redis→官方SDK/WS接收端 验证 Redis不可用的入站/投递语义；联合映射 E-06；命令 `["uv","run","pytest","-q","tests/acceptance/im_gateway/test_delivery.py","-k","b127"]`，不得以任务标题或静态声明代替行为证据。
- [ ] 执行上述契约命令，填写 Acceptance Evidence 的 RED/GREEN、每个关键断言位置、真实组件与清理记录；失败、skip 或外部阻塞保留未验证。所有代码改动有对应测试，函数≤50行，强类型与显式异常处理。

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| B-127 | E2E | 真实Worker/PG→Gateway HTTP→真实Redis→官方SDK/WS接收端 | 路由准确；首次发送/重复200不重发；TTL7d；失败可重试，Worker最多5次后FAILED；故障/不确定发送允许重复但不吞业务事实 | tests/acceptance/im_gateway/test_delivery.py / B-127（planned） | `["uv","run","pytest","-q","tests/acceptance/im_gateway/test_delivery.py","-k","b127"]` | planned |
| S-04 | E2E | 真实Worker→Gateway HTTP→Redis→官方SDK/真实WS接收 | delivery_key固定；按route推送最终结果；重放200/deduplicated=true且不重发 | tests/acceptance/im_gateway/test_delivery.py / S-04（planned） | `["uv","run","pytest","-q","tests/acceptance/im_gateway/test_delivery.py","-k","s04"]` | planned |
| E-06 | integration | Gateway入站/投递→真实Redis连接故障→Runtime/WS | 两条路径均at-least-once继续；故障时允许重复但不吞业务事实；恢复后去重恢复 | tests/acceptance/im_gateway/test_redis_degradation.py / E-06（planned） | `["uv","run","pytest","-q","tests/acceptance/im_gateway/test_redis_degradation.py","-k","e06"]` | planned |
| RULE-09 | E2E | 真实Worker/PG→Gateway HTTP→真实Redis→官方SDK/WS接收端 | 投递key、7d去重、200重放与降级；联合映射 S-04 / E-06 | tests/acceptance/im_gateway/test_delivery.py / RULE-09（planned） | `["uv","run","pytest","-q","tests/acceptance/im_gateway/test_delivery.py","-k","b127"]` | planned |
| RISK-02 | E2E | 真实Worker/PG→Gateway HTTP→真实Redis→官方SDK/WS接收端 | Redis不可用的入站/投递语义；联合映射 E-06 | tests/acceptance/im_gateway/test_delivery.py / RISK-02（planned） | `["uv","run","pytest","-q","tests/acceptance/im_gateway/test_delivery.py","-k","b127"]` | planned |

### Acceptance Evidence
> planned。编码期填 RED/GREEN 命令与结果、断言路径/用例/位置、真实组件证据、外部依赖状态与清理证据；全部 verified 才可 done。本次结构检查不代表功能测试通过。

### Log
- [2026-09-20] prepared (draft)
- [2026-09-21] 用户确认后写入；设计修订已承接，状态保持 draft。

---

## TASK-028: 验收 Secret 不泄露与单 Bot 隔离

- **Status**: draft
- **Priority**: P0
- **Depends**: TASK-006, TASK-007, TASK-017, TASK-018, TASK-021
- **Source**: 10-im-gateway.backend.design.md#2.5.2 功能验收场景, 10-im-gateway.backend.design.md#3.2.2 Bot 快照轮询与 Secret 解析, 10-im-gateway.backend.design.md#3.5 质量实现方案, 10-im-gateway.backend.design.md#Spec Compliance Matrix
- **Spec-Refs**: harness-secret#RULE-secret-001
- **Acceptance-Refs**: B-128, E-07, RULE-03, RISK-03, RULE-secret-001
- **Files**: `tests/acceptance/im_gateway/test_secrets_and_readiness.py`
- **Estimate**: 15–60 分钟；超出先拆分

### Description

在真实 DB 与受保护内部快照链路使用随机 canary secret，故障注入验证仅目标 bot 退避，其他 bot 正常；检查日志、审计、Snapshot、Prompt、外部API与IM响应。内部 bot 快照是设计指定的最小凭据传输边界，不扩散到公开端点。

### Checklist

- [ ] [B-128][integration] 修改生产代码前先按 真实PG bot secret→Console内部快照HTTP→Gateway/SDK→日志/审计/快照输出 编写或扩展用例并记录 RED；关键断言：缺失或错误secret不停止全部bot；readyz依据manager/完整快照而非全连接；所有禁止输出均无canary；SecretProvider无新增。执行 argv：`["uv","run","pytest","-q","tests/acceptance/im_gateway/test_secrets_and_readiness.py","-k","b128"]`。
- [ ] [E-07][integration] 修改生产代码前先按 真实PG bot secret→Console快照→多bot SDK连接/readyz 编写或扩展用例并记录 RED；关键断言：坏secret只影响目标bot并退避；其他bot正常；就绪详情降级且日志无secret。执行 argv：`["uv","run","pytest","-q","tests/acceptance/im_gateway/test_secrets_and_readiness.py","-k","e07"]`。
- [ ] 实现或补齐：在真实 DB 与受保护内部快照链路使用随机 canary secret，故障注入验证仅目标 bot 退避，其他 bot 正常；检查日志、审计、Snapshot、Prompt、外部API与IM响应。内部 bot 快照是设计指定的最小凭据传输边界，不扩散到公开端点。
- [ ] [RULE-secret-001][E2E] verifier_ref=harness-secret#RULE-secret-001；原 verifier 输入 argv=`["uv","run","pytest","-q","tests/test_logging_redaction.py","tests/acceptance/test_foundation_ops_audit.py"]`；补充真实边界 真实Worker投递/PG与Console快照→Gateway→官方SDK/WS；日志/审计/Snapshot/Prompt/对外响应，断言 缺失或错误secret不停止全部bot；readyz依据manager/完整快照而非全连接；所有禁止输出均无canary；SecretProvider无新增；原 verifier 全部通过；覆盖原Matrix S-04/E-07的主动投递与失败路径脱敏，联合验收 argv=`["bash","-lc","uv run pytest -q tests/acceptance/im_gateway/test_secrets_and_readiness.py && uv run pytest -q tests/test_logging_redaction.py tests/acceptance/test_foundation_ops_audit.py"]`。
- [ ] [RULE-03][integration] 作为唯一最终负责人，沿 真实PG bot secret→Console内部快照HTTP→Gateway/SDK→日志/审计/快照输出 验证 Owner表密钥存储与禁止输出；联合映射 S-01 / E-07；命令 `["uv","run","pytest","-q","tests/acceptance/im_gateway/test_secrets_and_readiness.py","-k","b128"]`，不得以任务标题或静态声明代替行为证据。
- [ ] [RISK-03][integration] 作为唯一最终负责人，沿 真实PG bot secret→Console内部快照HTTP→Gateway/SDK→日志/审计/快照输出 验证 WS抖动、secret失效、单bot隔离；联合映射 E-07；命令 `["uv","run","pytest","-q","tests/acceptance/im_gateway/test_secrets_and_readiness.py","-k","b128"]`，不得以任务标题或静态声明代替行为证据。
- [ ] 执行上述契约命令，填写 Acceptance Evidence 的 RED/GREEN、每个关键断言位置、真实组件与清理记录；失败、skip 或外部阻塞保留未验证。所有代码改动有对应测试，函数≤50行，强类型与显式异常处理。

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| B-128 | integration | 真实PG bot secret→Console内部快照HTTP→Gateway/SDK→日志/审计/快照输出 | 缺失或错误secret不停止全部bot；readyz依据manager/完整快照而非全连接；所有禁止输出均无canary；SecretProvider无新增 | tests/acceptance/im_gateway/test_secrets_and_readiness.py / B-128（planned） | `["uv","run","pytest","-q","tests/acceptance/im_gateway/test_secrets_and_readiness.py","-k","b128"]` | planned |
| E-07 | integration | 真实PG bot secret→Console快照→多bot SDK连接/readyz | 坏secret只影响目标bot并退避；其他bot正常；就绪详情降级且日志无secret | tests/acceptance/im_gateway/test_secrets_and_readiness.py / E-07（planned） | `["uv","run","pytest","-q","tests/acceptance/im_gateway/test_secrets_and_readiness.py","-k","e07"]` | planned |
| RULE-03 | integration | 真实PG bot secret→Console内部快照HTTP→Gateway/SDK→日志/审计/快照输出 | Owner表密钥存储与禁止输出；联合映射 S-01 / E-07 | tests/acceptance/im_gateway/test_secrets_and_readiness.py / RULE-03（planned） | `["uv","run","pytest","-q","tests/acceptance/im_gateway/test_secrets_and_readiness.py","-k","b128"]` | planned |
| RISK-03 | integration | 真实PG bot secret→Console内部快照HTTP→Gateway/SDK→日志/审计/快照输出 | WS抖动、secret失效、单bot隔离；联合映射 E-07 | tests/acceptance/im_gateway/test_secrets_and_readiness.py / RISK-03（planned） | `["uv","run","pytest","-q","tests/acceptance/im_gateway/test_secrets_and_readiness.py","-k","b128"]` | planned |
| RULE-secret-001 | E2E | 真实Worker投递/PG与Console快照→Gateway→官方SDK/WS；日志/审计/Snapshot/Prompt/对外响应 | 缺失或错误secret不停止全部bot；readyz依据manager/完整快照而非全连接；所有禁止输出均无canary；SecretProvider无新增；原 verifier 全部通过；覆盖原Matrix S-04/E-07的主动投递与失败路径脱敏 | tests/acceptance/im_gateway/test_secrets_and_readiness.py + 原 verifier / RULE-secret-001（planned） | `["bash","-lc","uv run pytest -q tests/acceptance/im_gateway/test_secrets_and_readiness.py && uv run pytest -q tests/test_logging_redaction.py tests/acceptance/test_foundation_ops_audit.py"]` | planned |

### Acceptance Evidence
> planned。编码期填 RED/GREEN 命令与结果、断言路径/用例/位置、真实组件证据、外部依赖状态与清理证据；全部 verified 才可 done。本次结构检查不代表功能测试通过。

### Log
- [2026-09-20] prepared (draft)
- [2026-09-21] 用户确认后写入；设计修订已承接，状态保持 draft。

---

## TASK-029: 收口全部场景、规则与验收证据

- **Status**: draft
- **Priority**: P0
- **Depends**: TASK-001, TASK-002, TASK-003, TASK-004, TASK-005, TASK-006, TASK-007, TASK-008, TASK-009, TASK-010, TASK-011, TASK-012, TASK-013, TASK-014, TASK-015, TASK-016, TASK-017, TASK-018, TASK-019, TASK-020, TASK-021, TASK-022, TASK-023, TASK-024, TASK-025, TASK-026, TASK-027, TASK-028
- **Source**: 10-im-gateway.backend.design.md#2.5.2 功能验收场景, 10-im-gateway.backend.design.md#5. 风险与依赖, 10-im-gateway.backend.design.md#6. 需求追溯矩阵, 10-im-gateway.backend.design.md#Spec Compliance Matrix
- **Spec-Refs**: harness-test#RULE-test-001
- **Acceptance-Refs**: B-129, RULE-07, RULE-test-001
- **Files**: `tests/acceptance/im_gateway/test_acceptance_inventory.py`
- **Estimate**: 15–60 分钟；超出先拆分

### Description

检查设计场景、业务规则、风险及 required Rule 唯一负责人与命令；执行原 Spec verifier 及本模块真实验收，登记每个断言位置、真实组件和数据清理；环境缺失或外部依赖未完成时保持未验收，不改变层级。

### Checklist

- [ ] [B-129][integration] 修改生产代码前先按 pytest用例收集/运行→验收Contract/Evidence→真实组件记录 编写或扩展用例并记录 RED；关键断言：无遗漏/重复最终负责人；新修复均有RED/GREEN；E2E无FakeAdapter/业务API mock；原verifier完整执行；失败/skip不冒充verified。执行 argv：`["uv","run","pytest","-q","tests/acceptance/im_gateway/test_acceptance_inventory.py","-k","b129"]`。
- [ ] 实现或补齐：检查设计场景、业务规则、风险及 required Rule 唯一负责人与命令；执行原 Spec verifier 及本模块真实验收，登记每个断言位置、真实组件和数据清理；环境缺失或外部依赖未完成时保持未验收，不改变层级。
- [ ] [RULE-test-001][E2E] verifier_ref=harness-test#RULE-test-001；原 verifier 输入 argv=`["bash","-lc","uv run pytest -q tests/acceptance && npm --prefix apps/console-platform/frontend run build && npm --prefix e2e test"]`；补充真实边界 真实生产HTTP/SSE、PostgreSQL、Redis、官方SDK/WS与原verifier Chrome→验收Evidence，断言 无遗漏/重复最终负责人；新修复均有RED/GREEN；E2E无FakeAdapter/业务API mock；原verifier完整执行；失败/skip不冒充verified；原 verifier 全部通过，联合验收 argv=`["bash","-lc","uv run pytest -q tests/acceptance && npm --prefix apps/console-platform/frontend run build && npm --prefix e2e test"]`。
- [ ] [RULE-07][E2E] 作为唯一最终负责人，沿 生产HTTP/SSE、PostgreSQL、Redis、官方SDK/WS与原verifier Chrome 验证 跨服务真实E2E证据；E-03保留原层级另有B-125 E2E增强；联合映射 S-01 / S-03 / E-03；命令 `["bash","-lc","uv run pytest -q tests/acceptance && npm --prefix apps/console-platform/frontend run build && npm --prefix e2e test"]`，不得以任务标题或静态声明代替行为证据。
- [ ] 执行上述契约命令，填写 Acceptance Evidence 的 RED/GREEN、每个关键断言位置、真实组件与清理记录；失败、skip 或外部阻塞保留未验证。所有代码改动有对应测试，函数≤50行，强类型与显式异常处理。

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| B-129 | integration | pytest用例收集/运行→验收Contract/Evidence→真实组件记录 | 无遗漏/重复最终负责人；新修复均有RED/GREEN；E2E无FakeAdapter/业务API mock；原verifier完整执行；失败/skip不冒充verified | tests/acceptance/im_gateway/test_acceptance_inventory.py / B-129（planned） | `["uv","run","pytest","-q","tests/acceptance/im_gateway/test_acceptance_inventory.py","-k","b129"]` | planned |
| RULE-07 | E2E | 生产HTTP/SSE、PostgreSQL、Redis、官方SDK/WS与原verifier Chrome | 跨服务真实E2E证据；E-03保留原层级另有B-125 E2E增强；联合映射 S-01 / S-03 / E-03 | tests/acceptance/im_gateway/test_acceptance_inventory.py / RULE-07（planned） | `["bash","-lc","uv run pytest -q tests/acceptance && npm --prefix apps/console-platform/frontend run build && npm --prefix e2e test"]` | planned |
| RULE-test-001 | E2E | 真实生产HTTP/SSE、PostgreSQL、Redis、官方SDK/WS与原verifier Chrome→验收Evidence | 无遗漏/重复最终负责人；新修复均有RED/GREEN；E2E无FakeAdapter/业务API mock；原verifier完整执行；失败/skip不冒充verified；原 verifier 全部通过 | tests/acceptance/im_gateway/test_acceptance_inventory.py + 原 verifier / RULE-test-001（planned） | `["bash","-lc","uv run pytest -q tests/acceptance && npm --prefix apps/console-platform/frontend run build && npm --prefix e2e test"]` | planned |

### Acceptance Evidence
> planned。编码期填 RED/GREEN 命令与结果、断言路径/用例/位置、真实组件证据、外部依赖状态与清理证据；全部 verified 才可 done。本次结构检查不代表功能测试通过。

### Log
- [2026-09-20] prepared (draft)
- [2026-09-21] 用户确认后写入；设计修订已承接，状态保持 draft。

---

## Plan Validation

- 正式任务文件：`.code-flow/tasks/2026-09-17/10-im-gateway/10-im-gateway.md`。
- Design 与 Plan 使用同一 persisted Context；8 条 required Rule 在 Design Matrix 与唯一 TASK item 绑定，不改变 enforcement 或原 verifier。
- 规划校验：Context validate、design gate、plan gate、Acceptance Coverage/Contract 一致性、依赖 DAG、真实章节引用与 argv 格式；证据只表示文档结构已检查，不表示功能 GREEN。
- 验收 manifest 由 `cf_acceptance_manifest.py` 生成并锁定 42 个 S/E/B 场景，状态全部 planned；Rule/Risk 唯一责任继续由本文件与 Spec gate 校验。
- 可复核命令：
  - `python3 .code-flow/scripts/cf_spec_context.py validate --task-dir .code-flow/tasks/2026-09-17/10-im-gateway --json`
  - `python3 .code-flow/scripts/cf_spec_gate.py --task-dir .code-flow/tasks/2026-09-17/10-im-gateway --stage design --json`
  - `python3 .code-flow/scripts/cf_spec_gate.py --task-dir .code-flow/tasks/2026-09-17/10-im-gateway --stage plan --artifact .code-flow/tasks/2026-09-17/10-im-gateway/10-im-gateway.md --json`
  - `python3 .code-flow/scripts/cf_acceptance_manifest.py --task-file .code-flow/tasks/2026-09-17/10-im-gateway/10-im-gateway.md --verify-plan --task-dir .code-flow/tasks/2026-09-17/10-im-gateway`
- 门禁通过后从 TASK-001（公共契约）或 TASK-020（真实 WS 测试边界）开始；TASK-021 建立环境后再执行依赖它的集成任务。External-Depends 必须在对应 TASK 启动前满足。
