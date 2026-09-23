# Tasks: IM Gateway 与主动投递

- **Source**: .code-flow/tasks/2026-09-17/10-im-gateway/（全部 design：10-im-gateway.backend.design.md）
- **Created**: 2026-09-20
- **Updated**: 2026-09-23
- **Plan-State**: planned（用户已确认写入；各 TASK 保持 draft，功能与 E2E 验收尚未执行）

## Proposal

补齐已有 Python IM Gateway 的多 Bot 连接、身份/命令、Runtime SSE 桥接和后台主动投递，修正现有实现与设计的契约差异。复用 Console 的身份/授权、Runtime 的 Run/Snapshot/取消、Worker 的持久投递事实，使 Gateway 保持渠道适配层职责，并以真实 HTTP/WS/PostgreSQL/Redis 验收跨模块结果。

共 32 个原子任务：P0 30、P1 2；依赖扁平化后本地依赖深度 8 层（TASK-029 为最深收口任务）。每项列出 1–3 个预计改动文件（含测试）：默认目标 15–60 分钟，跨服务环境、探针、基建与验收收口类任务按半天级标注（TASK-018/019/020/021/022/025/027/029/030/031/032）；外部服务启动与验收等待另计。超过范围先拆任务并校验 Context，禁止编码时静默扩展。B-101..B-131 是本次计划补充场景，已同步到 v1.3 design §2.5.3；原 13 个 S/E 场景保持 ID 与层级。

## Design Alignment

2026-09-21 用户确认继续并将任务写入本需求目录。下列局部设计修订已同步到 `10-im-gateway.backend.design.md` v1.2（并见下方 2026-09-23 的 v1.3 复核）；这是设计/计划承接，不代表实现或 verifier 已通过。

- **持久幂等**：Console bind 复用已存在的 `control.skill_import_idempotency`（Agent/MCP 已使用），固定 `endpoint=/internal/channel/bind`，保留 partial unique、指纹、首次成功响应及事务原子性，不新增表。Gateway 传原 message_id；同 key/同指纹 200 重放，异指纹按 required Rule 返回 409 `IDEMPOTENCY_MISMATCH`（指纹为规范化 JSON 的 SHA256，含 endpoint、tenant/actor、资源与关键参数）。2026-09-23 复核：Runtime/Worker/Console 现有幂等原语与该规则一致（不再存在待对齐的错误码差异）；`/new` 的重复提交同样由 Runtime 持久幂等。
- **列表契约**：API-01 Bot 快照保留 revision，补 items/page/page_size/total；跨页 revision/total 一致才发布完整快照，不一致保留旧快照到下一节拍重拉。API-04 Skills 在授权过滤后分页；page>=1、1<=page_size<=100。Console/Gateway 同步实现，禁止借“小集合”豁免 required Rule。
- **投递恢复**：API-05 成功键 TTL 7d，与 owner token 短租约分离；处理中不得当作 deduplicated 成功，明确发送失败释放租约并重试，崩溃到期恢复。Redis 不可用或外部发送结果不确定只承诺 at-least-once。原子去重/失败恢复由 EXT-09-021 实现，本模块承担消费契约与最终验收。
- **Spec 与验收**：Matrix 使用 Context 实际 spec_id、9 个 required Rule 的原 verifier_ref；覆盖 13 个原始场景、31 个补充场景（B-101..B-131）、10 条业务规则、3 个风险。每条 Spec Rule 唯一最终负责人见 Coverage / Contract，原 E2E 不降级；授权以 B-124 验证，Snapshot/CAS 以 B-125 验证。
- **就绪与密钥**：依据 manager 初始化、完整 snapshot 和事件循环判定 readiness；单 bot 故障在 detail 降级，不要求全部 CONNECTED。Secret 仅 Owner 表和 Console→Gateway 最小内部快照传输，禁止公开 API/日志/审计/Snapshot/Prompt/IM 回显，不引入 SecretProvider。

2026-09-23 Plan 复核后，下列修订已同步到 design v1.3（§1.2 修订历史、§2.5.3、§4.2、§5），并落到本文件的任务拆分与依赖：

- **依赖状态刷新**：EXT-09-020/021/043 已 satisfied（09-task-schedule 归档版 TASK-020/021/043 verified，实现已在仓库落地）；EXT-08 标注规则统一（凡消费真实 Runtime 契约的任务均登记）。
- **指标导出机制**：仓库无现存指标基建，本模块落地 api-kit 进程内注册表 + 真实 `GET /metrics`；B-118/B-119 的真实边界据此定义，不再引用未定义的 exporter。
- **环境与探针拆分**：TASK-021 收窄为「基础多服务环境」，多实例/Worker 扩到 TASK-030（B-130），WS/SDK 故障注入扩到 TASK-031（B-131）；TASK-025 的断流回收/Snapshot/CAS 分到 TASK-032，避免单任务跨度过大。
- **依赖扁平化**：TASK-002/003/004/005/008/009/010/013 不再依赖 TASK-021——其验收边界只需进程内 ASGI/本地 HTTP 服务 + 真实 PG/Redis（本仓库既有集成测试范式），无需多服务进程编排；TASK-015 的 WS 出站依赖改为 TASK-020（探针核心）。TASK-021 收窄为基础环境后，仅生命周期、多服务与 Runtime 契约相关任务（006/007/011/012/016/017/018/019/022–029/030/032）依赖它。
- **验收任务约定**：验收类 TASK 不对既有正确行为制造 RED；E2E 暴露缺陷时回退对应 owner TASK 修复后重新验收，不在验收任务内静默改生产代码。
- **幂等错误码对齐（spec 漂移修正）**：现行 required `harness-api#RULE-api-002` 规定同 key 异指纹返回 `IDEMPOTENCY_MISMATCH`、指纹为规范化 JSON（`sort_keys` + 紧凑分隔符）的 SHA256；本计划原写的 `COMMON_CONFLICT` 是旧规则文本的残留，已按现行规则改正（design API-03/API-06/Spec Matrix 同步）。Runtime/Worker/Console 现有实现（`run_submission.py`、`submissions.py`、`skill_service.py` 等）与该规则一致，TASK-026 不得再以「Runtime 待对齐」为由阻塞或改判。

## Baseline and External Dependencies

- 当前 Gateway 是 Python/FastAPI，已有 SDK Adapter、snapshot、inbound、dedupe、delivery 和测试，故本计划按现有代码补缺口，不另建 TypeScript Gateway。
- 已核实差异：投递为 exists→send→mark，响应字段 duplicate，Redis 检查失败直接报错；RuntimeClient 未传 Idempotency-Key；SSE 只保存 type/data；/stop 一律返回受理；任一 secret 错误会停止全部 bot；inbound 消费长流会阻塞后续命令；Console 缺 /internal/channel/skills，bind 未接 Header 幂等。
- **EXT-02/07**：Console 已有身份、AgentAccessGrant、Binding/Effective Capability；复用其事务和公式。TASK-003/004 是本计划明确的 Console Owner 补充工作，Gateway 不直接写 control/task 表。
- **EXT-08**：08-runtime-execution 的生产 Run HTTP/SSE、自动 resume、cancel-active、幂等、Snapshot、Reaper 必须可用；当前 Runtime 任务已标记 verified，但本模块仍需核对对应真实链路证据（原记录的幂等错误码差异已于 2026-09-23 按现行 spec 消除，见下）。TASK-009/013/015/016/022/023/024/025/032 依赖这些真实行为，不能拿直接更新 DB 的测试冒充 Gateway→Runtime E2E。凡消费真实 Runtime 契约的任务统一登记该外部依赖，避免门禁判定不一致。
- **EXT-09-020/021/043（已满足，2026-09-23 复核）**：09-task-schedule 已归档，其 TASK-020（持久 Final Delivery 抢占与重试）、TASK-021（Gateway 并发投递去重与失败恢复）、TASK-043（Worker 端最终投递验收）均为 verified，实现已在 `apps/im-gateway/src/muad_im_gateway/api/delivery.py`、`infrastructure/dedupe.py` 落地，证据见 09 归档文件的 Acceptance Coverage 与 commit `50d5edc`。故 TASK-017/027 不再以「外部任务未完成」为前提：本模块**复用**这些实现，只补响应契约对齐与跨模块 E2E，不重复实现、不得以 mock 代替。
- Task 文档命令从仓库根目录执行。表中测试/选择器均为 planned；既有测试文件也须补对应 ID 用例后才能执行验收。原 Spec verifier 命令保留原样，新增场景命令作为增强，不替代它。
- 真实边界：生产 Console/Gateway/Runtime/Worker 进程与 HTTP/SSE、PostgreSQL、Redis、官方 SDK、WS socket 不得 mock。企业微信服务端以本地真实 WS 协议探针承载，模型用现有真实 HTTP 探针；不声称企业微信生产账号实网已验证。单元测试可隔离纯逻辑，不能冒充 E2E。
- 所有新增/修复先补失败用例并记录 RED；已有正确行为先跑回归，不人为制造失败。**验收类 TASK（022–029、032）例外**：其前提是 owner 实现任务已完成并通过契约验收，不得为凑 RED 制造失败；执行中若暴露缺陷，回退到对应 owner TASK 修复（该回退边不入本文件 Depends DAG，按发现即登记处理），修复后重新执行验收命令，禁止在验收任务内静默修改生产代码。清理 e2e-im-* DB 数据/Redis keys/进程。环境缺失、skip 或外部任务未完成不能记 verified。
- **场景 ID 作用域**：B-/S-/E-/RULE-/RISK- 编号仅在本需求 Context 内唯一，与已归档 09-task-schedule 的同名 ID 不同义（例如 09 的 B-121/E-05 指 Gateway 投递去重与失败恢复，本文件的 B-121 指基础验收环境、E-05 指 NO_ACTIVE_RUN）。跨需求检索、grep 与证据引用必须带 Context 前缀（如 `10-im-gateway#B-121`），不得跨 Context 复用编号结论。
- **required Spec Rule 的跨 Context 归属**：本文件的 9 条 required Rule 唯一负责人仅在本 Context 内成立；09-task-schedule 对同批 Rule（含 RULE-test-001）在其 Context 内另有已 verified 的负责人。本模块的规则项必须执行自己的原 verifier + 补充真实边界，不得直接引用其他 Context 的 verified 结论充当本模块证据。
- **仓库级 verifier 的失败归因**：RULE-07 / RULE-test-001 的组合命令覆盖全仓 `tests/acceptance`、前端构建与 playwright 全量（`e2e/` 下多套 config），范围大于本模块。执行时必须逐项归因：与本模块无关的套件失败单列并回到其 owner，不得据以判定本模块场景通过或失败，也不得用其失败掩盖本模块自身失败。
- **外部依赖启动约束**：凡 External-Depends 未满足，启动该 TASK 前先核对 Owner 任务状态与对应验收证据；缺失时记录为 blocked，不把其他模块已 verified 当作协议差异已修复。当前 EXT-09-020/021/043 已满足（见上）；EXT-08 无待对齐错误码差异（idempotency 语义以 required RULE-api-002 为准），但真实 Runtime Run/SSE/Reaper 证据仍须在启动前核对。

## Task Overview

| TASK | 优先级 | 标题 | 依赖 | 来源章节 | 验收 | Checklist |
|---|---|---|---|---|---|---|
| TASK-001 | P0 | 收紧 Channel 与 Delivery 公共契约 | 无 | 3.3 数据设计；3.4 接口设计 | B-101(unit) | 3 |
| TASK-002 | P0 | 统一 Console 客户端封套解析与链路头 | 001 | 3.4 接口设计；3.5 质量实现方案 | B-102(integration) | 3 |
| TASK-003 | P0 | 补 Console bind 持久幂等与事务重放 | 001 | API-03 执行绑定；3.3 数据设计；Spec Compliance Matrix | B-103(integration) | 3 |
| TASK-004 | P0 | 补齐 Console Effective Skills 内部端点 | 001 | API-04 查询可用 Skills；2.5.1 业务规则与约束 | B-104(integration), RULE-data-001(integration) | 4 |
| TASK-005 | P0 | 补 Bot 快照轮询与热更新边界 | 001, 002 | 3.2.2 Bot 快照轮询与 Secret 解析；API-01 Bot 列表 | B-105(integration) | 3 |
| TASK-006 | P0 | 修复多 Bot 故障隔离与 WS 退避 | 005, 021, 031 | 3.2.1 WebSocket 连接状态机；3.2.2 Bot 快照轮询与 Secret 解析 | B-106(integration) | 3 |
| TASK-007 | P0 | 对齐启动、就绪与关闭语义 | 005, 006, 021 | 4.1 健康检查与启动校验；3.2.1 WebSocket 连接状态机 | B-107(integration) | 3 |
| TASK-008 | P0 | 对齐入站 Redis 原子去重与降级 | 001 | 3.2.3 入站去重 | B-108(integration), S-05(integration), RULE-08(integration) | 5 |
| TASK-009 | P0 | 对齐 resolve、未绑定与授权分流 | 001, 002 | API-02 解析消息路由；API-06 Runtime Run 桥接 | B-109(integration), E-01(integration) | 4 |
| TASK-010 | P0 | 完善 /bind 命令与稳定幂等键 | 002, 003 | 3.4.2 内置命令与文案映射（FEAT-02）；API-03 执行绑定 | B-110(integration) | 3 |
| TASK-011 | P0 | 对齐 /skills 与 /new 命令输出 | 002, 004, 009, 013, 021 | 3.4.2 内置命令与文案映射（FEAT-02）；API-04 查询可用 Skills | B-111(integration) | 3 |
| TASK-012 | P0 | 修复 /stop 已取消与取消中文案 | 009, 013, 021 | 3.4.2 内置命令与文案映射（FEAT-02） | B-112(integration), S-06(integration), E-05(integration) | 5 |
| TASK-013 | P0 | 补 Runtime 客户端幂等头与请求上下文 | 001 | API-06 Runtime Run 桥接；3.4.2 内置命令与文案映射（FEAT-02） | B-113(integration) | 3 |
| TASK-014 | P0 | 使 SSE 解析保留封套与序号 | 013 | 3.4.1 Runtime SSE 事件处理（FEAT-03） | B-114(unit) | 3 |
| TASK-015 | P0 | 补齐 SSE 到 IM 的收尾与中断呈现 | 014, 020 | 3.4.1 Runtime SSE 事件处理（FEAT-03）；3.4.2 内置命令与文案映射（FEAT-02） | B-115(integration) | 3 |
| TASK-016 | P0 | 避免长流阻塞后续消息与 /stop | 009, 010, 011, 012, 015, 021 | 3.2 架构与流程；API-06 Runtime Run 桥接；3.4.1 Runtime SSE 事件处理（FEAT-03） | B-116(integration) | 3 |
| TASK-017 | P0 | 对齐主动投递响应与渠道错误 | 001, 006, 021 | API-05 主动投递 | B-117(integration) | 3 |
| TASK-018 | P1 | 补连接状态指标与脱敏日志 | 006, 007, 021 | 4.2 指标目录；3.2.1 WebSocket 连接状态机；3.5 质量实现方案 | B-118(integration) | 3 |
| TASK-019 | P1 | 补消息、去重与投递指标 | 008, 015, 017, 018, 021 | 4.2 指标目录；3.5 质量实现方案 | B-119(integration) | 3 |
| TASK-020 | P0 | 建立真实 WS 探针核心与官方 SDK 边界 | 无 | 2.5.2 功能验收场景；3.1 技术选型与关键决策 | B-120(integration) | 3 |
| TASK-021 | P0 | 建立 Gateway 基础真实验收环境 | 020 | 2.5.2 功能验收场景；3.5 质量实现方案 | B-121(integration) | 3 |
| TASK-022 | P0 | 验收多 Bot 路由与任意 Runtime 实例 | 005, 006, 009, 013, 016, 021, 030 | 2.5.2 功能验收场景；3.1 技术选型与关键决策；Spec Compliance Matrix | B-122(E2E), S-01(E2E), RULE-01(E2E), RULE-04(E2E), RISK-01(E2E), RULE-arch-001(E2E), RULE-im-001(E2E) | 9 |
| TASK-023 | P0 | 验收绑定链路与双语 API 封套 | 002, 003, 010, 021 | 2.5.2 功能验收场景；API-03 执行绑定；3.4 接口设计；Spec Compliance Matrix | B-123(E2E), S-02(E2E), E-02(integration), RULE-02(E2E), RULE-api-001(E2E) | 7 |
| TASK-024 | P0 | 验收 Effective Capability 与命令权限 | 004, 009, 011, 021 | API-04 查询可用 Skills；API-02 解析消息路由；2.5.1 业务规则与约束；Spec Compliance Matrix | B-124(E2E), RULE-05(E2E), RULE-auth-001(E2E) | 5 |
| TASK-025 | P0 | 验收流式回复、Resume、取消与 Snapshot | 012, 014, 015, 016, 021 | 2.5.2 功能验收场景；3.4.1 Runtime SSE 事件处理（FEAT-03）；Spec Compliance Matrix | B-125(E2E), S-03(E2E), E-04(integration), RULE-10(E2E) | 6 |
| TASK-026 | P0 | 验收绑定和 Run 的端到端幂等 | 003, 010, 013, 021 | API-03 执行绑定；API-06 Runtime Run 桥接；Spec Compliance Matrix | B-126(E2E), RULE-api-002(E2E) | 4 |
| TASK-027 | P0 | 验收 Worker 主动投递及 Redis 故障 | 008, 017, 021, 030 | 2.5.2 功能验收场景；API-05 主动投递；3.2.3 入站去重；5. 风险与依赖 | B-127(E2E), S-04(E2E), E-06(integration), RULE-09(E2E), RISK-02(E2E) | 7 |
| TASK-028 | P0 | 验收 Secret 不泄露与单 Bot 隔离 | 006, 007, 017, 021 | 2.5.2 功能验收场景；3.2.2 Bot 快照轮询与 Secret 解析；3.5 质量实现方案；Spec Compliance Matrix | B-128(integration), E-07(integration), RULE-03(integration), RISK-03(integration), RULE-secret-001(E2E) | 7 |
| TASK-029 | P0 | 收口全部场景、规则与验收证据 | 001, 002, 003, 004, 005, 006, 007, 008, 009, 010, 011, 012, 013, 014, 015, 016, 017, 018, 019, 020, 021, 022, 023, 024, 025, 026, 027, 028, 030, 031, 032 | 2.5.2 功能验收场景；5. 风险与依赖；6. 需求追溯矩阵；Spec Compliance Matrix | B-129(integration), RULE-07(E2E), RULE-test-001(E2E) | 5 |
| TASK-030 | P0 | 补多实例与 Worker 环境扩展 | 021 | 2.5.2 功能验收场景；3.5 质量实现方案 | B-130(integration) | 3 |
| TASK-031 | P0 | 补 WS/SDK 故障注入边界 | 020 | 2.5.2 功能验收场景；3.1 技术选型与关键决策 | B-131(integration) | 3 |
| TASK-032 | P0 | 验收断流回收、Snapshot 冻结与终态 CAS | 015, 016, 021 | 2.5.2 功能验收场景；3.4.1 Runtime SSE 事件处理（FEAT-03）；Spec Compliance Matrix | E-03(integration), RULE-06(E2E), RULE-snapshot-001(E2E) | 5 |

## Acceptance Coverage

每个场景、业务 RULE、RISK 与 required Spec Rule 均只在一个 TASK 中作为最终负责人。跨任务实现依赖见 Depends；其他 TASK 可复核行为，但不重复登记最终 owner。

| 场景ID | 来源设计 | 测试层级 | 关键真实边界 | 负责任务 | 状态 | 执行命令 argv | cwd | timeout | depends_on |
|---|---|---|---|---|---|---|---|---|---|
| B-101 | 10-im-gateway.backend.design.md#3.3 数据设计 | unit | 真实 Pydantic DTO 校验与 JSON 序列化 | TASK-001 | verified | ["uv","run","pytest","-q","tests/gateway/test_channel_contracts.py","-k","b101"] | . | 600 |  |
| B-102 | 10-im-gateway.backend.design.md#3.4 接口设计 | integration | 生产 ConsoleClient→真实本地 HTTP 服务→Envelope 解码 | TASK-002 | verified | ["uv","run","pytest","-q","tests/gateway/test_gateway_console_client.py","-k","b102"] | . | 600 |  |
| B-103 | 10-im-gateway.backend.design.md#API-03 执行绑定 | integration | 真实 bind HTTP handler→PostgreSQL 幂等记录、bind_code 行锁、channel_identity | TASK-003 | verified | ["uv","run","pytest","-q","tests/console_channel/test_channel_bind_idempotency.py","-k","b103"] | . | 600 |  |
| B-104 | 10-im-gateway.backend.design.md#API-04 查询可用 Skills | integration | 真实 Console handler→生产授权服务→PostgreSQL Agent/Skill/Grant | TASK-004 | verified | ["uv","run","pytest","-q","tests/console_channel/test_channel_skills_api.py","-k","b104"] | . | 600 |  |
| RULE-data-001 | 10-im-gateway.backend.design.md#Spec Compliance Matrix | integration | 真实 PostgreSQL 表结构与约束（标准列、`is_deleted=false` partial unique、跨 Schema 逻辑 UUID）；原 Spec verifier 真实边界 | TASK-004 | planned | ["bash","-lc","uv run pytest -q tests/console_channel/test_channel_skills_api.py -k b104 && uv run pytest -q tests -k schema_parity"] | . | 600 |  |
| B-105 | 10-im-gateway.backend.design.md#3.2.2 Bot 快照轮询与 Secret 解析 | integration | Console snapshot HTTP→真实 PG bot 配置→BotSnapshotCache | TASK-005 | verified | ["uv","run","pytest","-q","tests/gateway/test_bot_snapshot.py","-k","b105"] | . | 600 |  |
| B-106 | 10-im-gateway.backend.design.md#3.2.1 WebSocket 连接状态机 | integration | 生产 WeComAdapter/连接管理器→真实本地 WS 故障探针 | TASK-006 | planned | ["uv","run","pytest","-q","tests/gateway/test_wecom_adapter.py","-k","b106"] | . | 600 |  |
| B-107 | 10-im-gateway.backend.design.md#4.1 健康检查与启动校验 | integration | 真实 Gateway lifespan/HTTP probes→Console/WS 连接管理器 | TASK-007 | planned | ["uv","run","pytest","-q","tests/gateway/test_readyz.py","-k","b107"] | . | 600 |  |
| B-108 | 10-im-gateway.backend.design.md#3.2.3 入站去重 | integration | Gateway inbound→真实 Redis→真实 Runtime HTTP 接收边界 | TASK-008 | verified | ["uv","run","pytest","-q","tests/gateway/test_inbound_dedupe_integration.py","-k","b108"] | . | 600 |  |
| S-05 | 10-im-gateway.backend.design.md#2.5.2 功能验收场景 | integration | Gateway入站→真实Redis dedupe→真实下游HTTP观测 | TASK-008 | verified | ["uv","run","pytest","-q","tests/gateway/test_inbound_dedupe_integration.py","-k","s05"] | . | 600 |  |
| RULE-08 | 10-im-gateway.backend.design.md#2.5.1 业务规则与约束 | integration | Gateway inbound→真实 Redis→真实 Runtime HTTP 接收边界 | TASK-008 | planned | ["uv","run","pytest","-q","tests/gateway/test_inbound_dedupe_integration.py","-k","b108"] | . | 600 |  |
| B-109 | 10-im-gateway.backend.design.md#API-02 解析消息路由 | integration | Gateway→真实 Console resolve HTTP→PostgreSQL→Runtime 接收观测 | TASK-009 | verified | ["uv","run","pytest","-q","tests/gateway/test_message_routing_integration.py","-k","b109"] | . | 600 |  |
| E-01 | 10-im-gateway.backend.design.md#2.5.2 功能验收场景 | integration | Gateway→真实bot resolve HTTP→PostgreSQL | TASK-009 | verified | ["uv","run","pytest","-q","tests/gateway/test_message_routing_integration.py","-k","e01"] | . | 600 |  |
| B-110 | 10-im-gateway.backend.design.md#3.4.2 内置命令与文案映射（FEAT-02） | integration | Gateway command→真实 Console bind HTTP→PostgreSQL | TASK-010 | verified | ["uv","run","pytest","-q","tests/gateway/test_bind_command.py","-k","b110"] | . | 600 |  |
| B-111 | 10-im-gateway.backend.design.md#3.4.2 内置命令与文案映射（FEAT-02） | integration | Gateway commands→真实 Console/Runtime HTTP→PostgreSQL | TASK-011 | planned | ["uv","run","pytest","-q","tests/gateway/test_commands_integration.py","-k","b111"] | . | 600 |  |
| B-112 | 10-im-gateway.backend.design.md#3.4.2 内置命令与文案映射（FEAT-02） | integration | Gateway→真实 Runtime cancel-active HTTP→PostgreSQL CAS/事件 | TASK-012 | planned | ["uv","run","pytest","-q","tests/gateway/test_stop_integration.py","-k","b112"] | . | 600 |  |
| S-06 | 10-im-gateway.backend.design.md#2.5.2 功能验收场景 | integration | 真实Gateway→Runtime cancel-active HTTP→PostgreSQL CAS | TASK-012 | planned | ["uv","run","pytest","-q","tests/gateway/test_stop_integration.py","-k","s06"] | . | 600 |  |
| E-05 | 10-im-gateway.backend.design.md#2.5.2 功能验收场景 | integration | 真实Runtime cancel-active/PG→Gateway | TASK-012 | planned | ["uv","run","pytest","-q","tests/gateway/test_stop_integration.py","-k","e05"] | . | 600 |  |
| B-113 | 10-im-gateway.backend.design.md#API-06 Runtime Run 桥接 | integration | 生产 RuntimeClient→真实本地 HTTP/SSE 接收端 | TASK-013 | planned | ["uv","run","pytest","-q","tests/gateway/test_runtime_client.py","-k","b113"] | . | 600 |  |
| B-114 | 10-im-gateway.backend.design.md#3.4.1 Runtime SSE 事件处理（FEAT-03） | unit | 真实 SSE parser 与分片字节/行输入 | TASK-014 | planned | ["uv","run","pytest","-q","tests/gateway/test_sse_parser.py","-k","b114"] | . | 600 |  |
| B-115 | 10-im-gateway.backend.design.md#3.4.1 Runtime SSE 事件处理（FEAT-03） | integration | 真实 SSE 解析→生产 renderer→真实本地 WS SDK 出站 | TASK-015 | planned | ["uv","run","pytest","-q","tests/gateway/test_stream_renderer.py","-k","b115"] | . | 600 |  |
| B-116 | 10-im-gateway.backend.design.md#3.2 架构与流程 | integration | 真实 iter_events→Gateway 消费队列→Runtime HTTP/SSE→WS 回复 | TASK-016 | planned | ["uv","run","pytest","-q","tests/gateway/test_inbound_concurrency.py","-k","b116"] | . | 600 |  |
| B-117 | 10-im-gateway.backend.design.md#API-05 主动投递 | integration | 真实 Gateway HTTP→生产 Adapter→真实 Redis/本地 WS | TASK-017 | planned | ["uv","run","pytest","-q","tests/gateway/test_delivery_api.py","-k","b117"] | . | 600 |  |
| B-118 | 10-im-gateway.backend.design.md#4.2 指标目录 | integration | 真实连接迁移→生产日志 + 真实 `/metrics` HTTP 端点（api-kit 注册表） | TASK-018 | planned | ["uv","run","pytest","-q","tests/gateway/test_connection_observability.py","-k","b118"] | . | 600 |  |
| B-119 | 10-im-gateway.backend.design.md#4.2 指标目录 | integration | 生产入站/HTTP投递/真实SSE→真实 `/metrics` HTTP 端点（api-kit 注册表） | TASK-019 | planned | ["uv","run","pytest","-q","tests/gateway/test_message_metrics.py","-k","b119"] | . | 600 |  |
| B-120 | 10-im-gateway.backend.design.md#2.5.2 功能验收场景 | integration | 官方 SDK→真实本地 WebSocket 服务→生产 WeComAdapter（探针核心：认证/消息/流式收发） | TASK-020 | verified | ["uv","run","pytest","-q","tests/acceptance/im_gateway/test_wecom_boundary.py","-k","b120"] | . | 600 |  |
| B-121 | 10-im-gateway.backend.design.md#2.5.2 功能验收场景 | integration | 生产进程生命周期→真实HTTP/PostgreSQL/Redis（Console/Gateway/模型探针/种子与清理） | TASK-021 | verified | ["uv","run","pytest","-q","tests/acceptance/im_gateway/test_environment.py","-k","b121"] | . | 600 |  |
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
| RULE-api-001 | 10-im-gateway.backend.design.md#Spec Compliance Matrix | E2E | 真实WS→Gateway命令→Console bind HTTP→PostgreSQL→SDK回复；分页契约（B-101/B-105）与统一列表语义；原 Spec verifier 真实边界 | TASK-023 | planned | ["bash","-lc","uv run pytest -q tests/acceptance/im_gateway/test_binding.py && uv run pytest -q tests/gateway/test_channel_contracts.py -k b101 && uv run pytest -q tests/gateway/test_bot_snapshot.py -k b105 && uv run pytest -q tests/test_api_i18n.py tests/test_error_catalog.py tests/acceptance/test_foundation_api_envelope.py"] | . | 1200 |  |
| B-124 | 10-im-gateway.backend.design.md#API-04 查询可用 Skills | E2E | WS命令→Gateway→Console授权HTTP/PG→Runtime Prompt/ToolRegistry | TASK-024 | planned | ["uv","run","pytest","-q","tests/acceptance/im_gateway/test_authorization.py","-k","b124"] | . | 1200 |  |
| RULE-05 | 10-im-gateway.backend.design.md#2.5.1 业务规则与约束 | E2E | WS命令→Gateway→Console授权HTTP/PG→Runtime Prompt/ToolRegistry | TASK-024 | planned | ["uv","run","pytest","-q","tests/acceptance/im_gateway/test_authorization.py","-k","b124"] | . | 1200 |  |
| RULE-auth-001 | 10-im-gateway.backend.design.md#Spec Compliance Matrix | E2E | WS命令→Gateway→Console授权HTTP/PG→Runtime Prompt/ToolRegistry；原 Spec verifier 真实边界 | TASK-024 | planned | ["bash","-lc","uv run pytest -q tests/acceptance/im_gateway/test_authorization.py && uv run pytest -q tests/console_platform/test_user_side_relations.py -k s04 && uv run pytest -q tests -k schema_parity"] | . | 1200 |  |
| B-125 | 10-im-gateway.backend.design.md#2.5.2 功能验收场景 | E2E | 真实WS→Gateway→Runtime HTTP/SSE→PostgreSQL Snapshot/Reaper→SDK回复 | TASK-025 | planned | ["uv","run","pytest","-q","tests/acceptance/im_gateway/test_runtime_stream.py","-k","b125"] | . | 1200 |  |
| S-03 | 10-im-gateway.backend.design.md#2.5.2 功能验收场景 | E2E | 真实WeCom协议WS→Gateway→Runtime SSE/PG→官方SDK出站 | TASK-025 | planned | ["uv","run","pytest","-q","tests/acceptance/im_gateway/test_runtime_stream.py","-k","s03"] | . | 1200 |  |
| E-03 | 10-im-gateway.backend.design.md#2.5.2 功能验收场景 | integration | Gateway→真实Runtime SSE断线→Reaper/PostgreSQL | TASK-032 | planned | ["uv","run","pytest","-q","tests/acceptance/im_gateway/test_recovery.py","-k","e03"] | . | 600 |  |
| E-04 | 10-im-gateway.backend.design.md#2.5.2 功能验收场景 | integration | 真实Runtime/PG→Gateway HTTP错误处理 | TASK-025 | planned | ["uv","run","pytest","-q","tests/acceptance/im_gateway/test_runtime_stream.py","-k","e04"] | . | 600 |  |
| RULE-06 | 10-im-gateway.backend.design.md#2.5.1 业务规则与约束 | integration | 真实WS→Gateway→Runtime SSE断流→PostgreSQL Snapshot/Reaper→终态CAS | TASK-032 | planned | ["uv","run","pytest","-q","tests/acceptance/im_gateway/test_recovery.py","-k","e03"] | . | 600 |  |
| RULE-10 | 10-im-gateway.backend.design.md#2.5.1 业务规则与约束 | E2E | 真实WS→Gateway→Runtime HTTP/SSE→PostgreSQL Snapshot/Reaper→SDK回复 | TASK-025 | planned | ["uv","run","pytest","-q","tests/acceptance/im_gateway/test_runtime_stream.py","-k","b125"] | . | 1200 |  |
| RULE-snapshot-001 | 10-im-gateway.backend.design.md#Spec Compliance Matrix | E2E | 真实WS→Gateway→Runtime SSE断流/Recovery→PostgreSQL Snapshot/Reaper→终态CAS；原 Spec verifier 真实边界 | TASK-032 | planned | ["bash","-lc","uv run pytest -q tests/acceptance/im_gateway/test_recovery.py && uv run pytest -q tests/agent_runtime -k \"executor or resolve\""] | . | 1200 |  |
| B-126 | 10-im-gateway.backend.design.md#API-03 执行绑定 | E2E | Gateway HTTP→Console/Runtime→真实PostgreSQL幂等表/partial unique→可观测副作用 | TASK-026 | planned | ["uv","run","pytest","-q","tests/acceptance/im_gateway/test_idempotency.py","-k","b126"] | . | 1200 |  |
| RULE-api-002 | 10-im-gateway.backend.design.md#Spec Compliance Matrix | E2E | Gateway HTTP→Console/Runtime→真实PostgreSQL幂等表/partial unique→可观测副作用（含 B-103 绑定幂等、B-111 /new 幂等）；原 Spec verifier 真实边界 | TASK-026 | planned | ["bash","-lc","uv run pytest -q tests/acceptance/im_gateway/test_idempotency.py && uv run pytest -q tests/console_channel/test_channel_bind_idempotency.py -k b103 && uv run pytest -q tests/gateway/test_commands_integration.py -k b111 && uv run pytest -q tests/console_skill/test_import_idempotency.py"] | . | 1200 |  |
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
| B-130 | 10-im-gateway.backend.design.md#2.5.2 功能验收场景 | integration | 生产第二 Runtime 实例与 Worker 进程→真实 PostgreSQL/Redis | TASK-030 | planned | ["uv","run","pytest","-q","tests/acceptance/im_gateway/test_multi_instance.py","-k","b130"] | . | 600 |  |
| B-131 | 10-im-gateway.backend.design.md#2.5.2 功能验收场景 | integration | 生产 WeComAdapter→真实本地 WS 服务→故障注入（握手拒绝/断线/发送失败） | TASK-031 | planned | ["uv","run","pytest","-q","tests/acceptance/im_gateway/test_wecom_fault_injection.py","-k","b131"] | . | 600 |  |
| RULE-07 | 10-im-gateway.backend.design.md#2.5.1 业务规则与约束 | E2E | 生产HTTP/SSE、PostgreSQL、Redis、官方SDK/WS与原verifier Chrome | TASK-029 | planned | ["bash","-lc","uv run pytest -q tests/acceptance && npm --prefix apps/console-platform/frontend run build && npm --prefix e2e test"] | . | 1200 |  |
| RULE-test-001 | 10-im-gateway.backend.design.md#Spec Compliance Matrix | E2E | 真实生产HTTP/SSE、PostgreSQL、Redis、官方SDK/WS与原verifier Chrome→验收Evidence | TASK-029 | planned | ["bash","-lc","uv run pytest -q tests/acceptance && npm --prefix apps/console-platform/frontend run build && npm --prefix e2e test"] | . | 1200 |  |

## Rule and Risk Traceability

| 设计约束 / 风险 | 关联场景 | 最终负责人 |
|---|---|---|
| RULE-01：四部署单元、Runtime/Worker无状态与任意实例 | S-01, B-122 + 原 verifier | TASK-022 |
| RULE-02：统一封套、catalog错误码及分页 | S-02, E-02, B-123 + 原 verifier | TASK-023 |
| RULE-03：Owner表密钥存储与禁止输出 | S-01, E-07, B-128 | TASK-028 |
| RULE-04：Agent 0..N bot，bot只路由一个Agent，无Pod绑定 | S-01, E-01, B-122 | TASK-022 |
| RULE-05：三层授权与Effective Capability；补充B-124避免仅RUN_BUSY冒充授权验证 | S-03, E-04, B-124 | TASK-024 |
| RULE-06：Snapshot冻结与终态CAS | S-03, E-03, B-125 | TASK-032 |
| RULE-07：跨服务真实E2E证据；E-03保留原层级另有B-125 E2E增强；B-130/B-131 为环境与探针补充证据 | S-01, S-03, E-03, B-130, B-131 | TASK-029 |
| RULE-08：入站Redis SET NX EX600、降级at-least-once | S-05, E-06 | TASK-008 |
| RULE-09：投递key、7d去重、200重放与降级 | S-04, E-06, B-127 | TASK-027 |
| RULE-10：未绑定正常分支、自动resume、并发和取消错误语义 | S-06, E-04, E-05, B-125 | TASK-025 |
| RISK-01：SDK类型隔离，iter_events唯一入口 | S-01, B-122 | TASK-022 |
| RISK-02：Redis不可用的入站/投递语义 | E-06, B-127 | TASK-027 |
| RISK-03：WS抖动、secret失效、单bot隔离 | E-07, B-131 | TASK-028 |

**本轮局部 Plan 新增 required Rule 归属**：`harness-data#RULE-data-001`（TASK-004 改动 repository/持久化路径时由路径映射自动纳入 Context）—— 唯一负责人 TASK-004，验证 真实 PostgreSQL 表结构与约束 + 原 verifier `-k schema_parity`；覆盖行见 Acceptance Coverage。

**同命令承载多条义务（执行一次须同时核对全部断言，任一断言缺失即该组整体不通过）**：`-k b122` = B-122 / RULE-01 / RULE-04 / RISK-01；`-k b108` = B-108 / RULE-08；`-k b127` = B-127 / RULE-09 / RISK-02；`-k b128` = B-128 / RULE-03 / RISK-03；`-k e03` = E-03 / RULE-06。规则项的最终负责人按上表登记，联合映射的其他场景（如 RULE-03 映射 S-01、RULE-10 映射 E-05）只是复核关系，不重复登记 owner。

---

## TASK-001: 收紧 Channel 与 Delivery 公共契约

- **Status**: done
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

- [x] [B-101][unit] 修改生产代码前先按 真实 Pydantic DTO 校验与 JSON 序列化 编写或扩展用例并记录 RED；关键断言：合法/缺字段/非法 UUID/未知枚举；deduplicated 字段；Secret 不因 repr/错误详情外泄；页码边界。执行 argv：`["uv","run","pytest","-q","tests/gateway/test_channel_contracts.py","-k","b101"]`。
- [x] 实现或补齐：补齐 resolve 的 external_conversation_id、强类型 Skill/Delivery 响应；限定 message.type=text，验证 delivery_key 中 task_id 与请求一致、未知 channel 安全拒绝。Bot 快照/Skills 分页字段按 v1.2 设计采用统一 Page 语义，快照保留 revision。
- [x] 执行上述契约命令，填写 Acceptance Evidence 的 RED/GREEN、每个关键断言位置、真实组件与清理记录；失败、skip 或外部阻塞保留未验证。所有代码改动有对应测试，函数≤50行，强类型与显式异常处理。

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| B-101 | unit | 真实 Pydantic DTO 校验与 JSON 序列化 | 合法/缺字段/非法 UUID/未知枚举；deduplicated 字段；Secret 不因 repr/错误详情外泄；页码边界 | tests/gateway/test_channel_contracts.py / B-101（verified） | `["uv","run","pytest","-q","tests/gateway/test_channel_contracts.py","-k","b101"]` | verified |

### Acceptance Evidence

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|---|---|---|---|---|---|
| B-101 | `uv run pytest -q tests/gateway/test_channel_contracts.py -k b101` → collection ImportError：`cannot import name 'DEFAULT_PAGE_SIZE' from 'muad_contracts'`（新契约符号与校验尚未实现） | `uv run pytest -q tests/gateway/test_channel_contracts.py -k b101` → `7 passed in 0.01s` | `test_b101_resolve_requires_known_channel_and_optional_conversation`（未知 channel/缺字段拒绝 + external_conversation_id 序列化）；`test_b101_snapshot_keeps_revision_and_unified_page_semantics`（revision 保留、page/page_size/total 默认与 JSON、页码边界 page=0/-1、page_size=0/101/-5 拒绝、page_size=100 通过）；`test_b101_skills_response_is_typed_and_paged`（允许字段集合恰为 skill_id/key/name/platform_label/description，多传 `prompt` 全文被 extra=forbid 拒绝）；`test_b101_delivery_message_type_is_text_only`（type 仅 text）；`test_b101_delivery_key_must_match_request_task_id`（key↔task_id 一致性、非法 UUID/错误前缀拒绝）；`test_b101_delivery_response_exposes_deduplicated_flag`（accepted/delivered/deduplicated 必填与 JSON）；`test_b101_secret_never_appears_in_repr_or_validation_errors`（repr/str/校验异常均无 canary，`model_dump_json` 仍携带用于内部快照线缆） | 真实 Pydantic v2 模型（`packages/contracts`），无 stub/mock；`model_dump_json`/`model_validate` 真实序列化路径 | verified |

补充记录：
- 实现范围：`channel.py` 新增 `PageMeta`（page>=1、1<=page_size<=100、total>=0，边界与 api-kit `muad_api.response` 常量一致）、`ChannelSkillItem`/`ChannelSkillsResponse`、`ChannelResolveRequest.external_conversation_id`、`BotSnapshotItem.secret` 改 `repr=False`；`delivery.py` 的 `DeliveryMessage.type` 收紧为 `Literal["text"]`、`DeliveryRequest` 增加 key↔task_id 一致性校验、新增 `DeliveryResponse(accepted/delivered/deduplicated)`；三者经 `__init__` 导出。
- 回归：`uv run pytest -q tests --ignore=tests/acceptance` → `1131 passed`。收紧契约后暴露既有测试构造错误：`tests/gateway/test_delivery_api.py` 的 `delivery_body()` 用随机 task_id 拼 delivery_key，已修正该 helper（新增 task_id 参数）与 e05 调用点，仅测试侧改动，生产投递实现未动（响应契约对齐属 TASK-017）。
- 外部依赖：无（本任务不消费 Runtime/Worker 契约，EXT 不适用）。该仓库级 required Rule 的唯一负责人仍是 TASK-029，本任务只记录运行现象，不重复登记归属。
- 仓库级 verifier 现象（据实记录；归因已更正）：仓库级 required Rule（`harness-test` 的 `RULE-test-001`）的组合命令中 `tests/acceptance` 曾在 4 次运行中不稳定失败（失败点每次不同：`test_batch.py::test_s04_*` 两次、`test_execution.py::test_b141_*`、`test_delivery.py::test_e05_*`，均为 worker 执行 Skill 时 `SKILL_ARTIFACT_UNAVAILABLE`；单独或小范围连跑均通过，与本次契约改动无关——对照实验带改动/回退 contracts/再恢复单跑 3 次全 pass）。**根因是执行环境残留而非 09 模块缺陷**：早期把长时间运行的 verifier 输出接进 `head` 触发 SIGPIPE，打断了 pytest 收尾，留下 Console/Worker 孤儿进程；孤儿进程继续连接同一测试库，用已被删除的临时 `ARTIFACT_ROOT` 执行新提交任务，正好产生该错误码。清掉孤儿进程（`ps aux | grep uvicorn/muad_*`，共 4 个）与 Redis 残留后，同一门禁**一次通过**（acceptance 全绿 + 前端 build + playwright 4 passed）。教训：运行验收套件不要用会被提前关闭的管道（`| head`），必须捕获完整输出；启动前先确认无残留进程。
- 清理：纯契约与单测，无 DB/Redis/进程副作用。
- 未覆盖说明：Bot 快照/Skills 的**真实分页取值与跨页 revision 一致**由 TASK-005（B-105）/TASK-004（B-104）验收；本任务只固定契约字段与边界。
- B-101: verified — automated command passed; run_id=76e32fdc4c134802bb4d7942c6f73356 (confirmed_by: runner)
- B-101: verified — automated command passed; run_id=ffac033b6d984b9b879077a7ab3175a3 (confirmed_by: runner)
- B-101: verified — automated command passed; run_id=ac0ac10ac1b6436f9fee088d2b0922ce (confirmed_by: runner)
- B-101: verified — automated command passed; run_id=0596003af84e47daab8f58b11ab6e4b8 (confirmed_by: runner)

### Log
- [2026-09-20] prepared (draft)
- [2026-09-21] 用户确认后写入；设计修订已承接，状态保持 draft。
- [2026-09-23] started
- [2026-09-23] 契约收紧 + B-101 verified（RED: 收集期 ImportError → GREEN: 7 passed；全量非验收回归 1131 passed）

---
- [2026-09-23] completed (done)
## TASK-002: 统一 Console 客户端封套解析与链路头

- **Status**: done
- **Priority**: P0
- **Depends**: TASK-001
- **Source**: 10-im-gateway.backend.design.md#3.4 接口设计, 10-im-gateway.backend.design.md#3.5 质量实现方案
- **Spec-Refs**: 
- **Acceptance-Refs**: B-102
- **Files**: `apps/im-gateway/src/muad_im_gateway/application/console_client.py`, `apps/im-gateway/src/muad_im_gateway/application/envelope.py`, `tests/gateway/test_gateway_console_client.py`
- **Estimate**: 15–60 分钟；超出先拆分

### Description

使用强类型 data 解析与 API 契约；透传 tenant/trace/request，依赖失败显式映射，移除 /skills 404 静默返回空目录；不用宽泛 Any 掩盖契约错误。

### Checklist

- [x] [B-102][integration] 修改生产代码前先按 生产 ConsoleClient→真实本地 HTTP 服务→Envelope 解码 编写或扩展用例并记录 RED；关键断言：headers 保持；有效空目录区别于 404/坏 JSON/超时；错误 code 保留；内部 URL/响应体不回显。执行 argv：`["uv","run","pytest","-q","tests/gateway/test_gateway_console_client.py","-k","b102"]`。
- [x] 实现或补齐：使用强类型 data 解析与 API 契约；透传 tenant/trace/request，依赖失败显式映射，移除 /skills 404 静默返回空目录；不用宽泛 Any 掩盖契约错误。
- [x] 执行上述契约命令，填写 Acceptance Evidence 的 RED/GREEN、每个关键断言位置、真实组件与清理记录；失败、skip 或外部阻塞保留未验证。所有代码改动有对应测试，函数≤50行，强类型与显式异常处理。

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| B-102 | integration | 生产 ConsoleClient→真实本地 HTTP 服务→Envelope 解码 | headers 保持；有效空目录区别于 404/坏 JSON/超时；错误 code 保留；内部 URL/响应体不回显 | tests/gateway/test_gateway_console_client.py / B-102（verified） | `["uv","run","pytest","-q","tests/gateway/test_gateway_console_client.py","-k","b102"]` | verified |

### Acceptance Evidence

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|---|---|---|---|---|---|
| B-102 | `uv run pytest -q tests/gateway/test_gateway_console_client.py -k b102` → `4 failed, 3 passed`：trace/request 未透传；404 被当作空目录（DID NOT RAISE）；封套 data 未走强类型（DID NOT RAISE）；空目录未走分页契约 | 同一命令 → `7 passed` | `test_b102_propagates_tenant_trace_and_request_headers`（真实服务端回读 X-Tenant-Id/X-Trace-Id/X-Request-Id/X-Caller-Service，trace/request 取自 `muad_api.context`）；`test_b102_effective_empty_catalog_is_a_valid_response`（空目录 = 合法响应，区别于错误）；`test_b102_not_found_is_an_error_not_an_empty_catalog`（404 → AppError，code=AGENT_ACCESS_DENIED，且不含内部 msg/URL）；`test_b102_bad_envelope_is_an_error_not_an_empty_catalog`（裸数组/缺 data → COMMON_INTERNAL_ERROR）；`test_b102_timeout_is_an_error_not_an_empty_catalog`（httpx 超时 → COMMON_INTERNAL_ERROR）；`test_b102_error_code_is_preserved_and_internal_details_are_not_echoed`（BOT_NOT_FOUND 保留、`data is None`、无 URL/响应体回显）；`test_b102_skills_response_is_typed_for_contract_errors`（缺 skill_id/key 显式失败）；既有 `test_channel_skills_returns_typed_paged_response` / `test_channel_skills_404_is_an_error` / `test_channel_skills_rejects_bare_list_envelope` 同步覆盖 | 真实本地 HTTP 服务：`uvicorn.Server` 线程 + 真实 socket（`127.0.0.1:<随机端口>`），断言基于真实 HTTP 响应解码，**未使用 MockTransport 绕过**（既有 MockTransport 用例保留为纯逻辑回归） | verified |

补充记录：
- 实现范围：`envelope.py` 新增 `require_data_model`（真实封套 → 强类型 data；坏 JSON/缺 data/字段不符统一显式失败，异常只带稳定 code，不回显内部 URL/响应体），删除因本次改动失效的 `require_data_list`；`console_client.py` 四个端点统一走 `_request_model`（强类型 + 显式依赖失败映射），`channel_skills` 返回 `ChannelSkillsResponse` 并移除 404→空目录回退，`_headers` 补 trace/request 透传。
- 连带（类型跟随，已在回归中覆盖）：`tests/gateway/fakes.py` 的 `FakeConsoleClient.channel_skills` 改为返回 `ChannelSkillsResponse`；`inbound.py` 的 `format_skills` 改收 `Sequence[ChannelSkillItem]`（属性访问）；`tests/gateway/test_inbound.py` 的 skills 夹具补 `skill_id`/`key`（契约收紧后必须合法）。/skills 命令的最终文案与按 `total` 有界翻页属 TASK-011（B-111），本任务只落单页强类型契约。
- 回归：`uv run pytest -q tests --ignore=tests/acceptance` → `1138 passed`；`tests/gateway` → `147 passed`。
- 外部依赖：无（本任务只用 Console 契约，不消费 Runtime/Worker）。
- 清理：uvicorn 线程在 fixture finally 中 `should_exit` 并 join；无残留进程、DB 数据或 Redis 键。
- B-102: verified — automated command passed; run_id=1f48312b4d9547309217898537533cf8 (confirmed_by: runner)

### Log
- [2026-09-20] prepared (draft)
- [2026-09-21] 用户确认后写入；设计修订已承接，状态保持 draft。

---
- [2026-09-23] started
- [2026-09-23] completed (done)
## TASK-003: 补 Console bind 持久幂等与事务重放

- **Status**: done
- **Priority**: P0
- **Depends**: TASK-001
- **Source**: 10-im-gateway.backend.design.md#API-03 执行绑定, 10-im-gateway.backend.design.md#3.3 数据设计, 10-im-gateway.backend.design.md#Spec Compliance Matrix
- **Spec-Refs**: 
- **Acceptance-Refs**: B-103
- **Files**: `apps/console-platform/backend/src/muad_console_platform/api/internal_channel.py`, `apps/console-platform/backend/src/muad_console_platform/application/channel_service.py`, `tests/console_channel/test_channel_bind_idempotency.py`
- **Estimate**: 15–60 分钟；超出先拆分

### Description

在 Console Owner 事务内复用现有幂等存储原语：同 key/同指纹重放首次响应，异指纹 `IDEMPOTENCY_MISMATCH`（与既有 skill/agent/mcp 幂等原语一致）；无同 key 重放时已用绑定码仍 BIND_CODE_INVALID；绑定不自动授予 Agent 权限。复用 control.skill_import_idempotency，endpoint 固定 /internal/channel/bind；已核实包含租户/key/endpoint partial unique、request_fingerprint 与 response_json，不新增迁移或 Gateway 业务表。

### Checklist

- [x] [B-103][integration] 修改生产代码前先按 真实 bind HTTP handler→PostgreSQL 幂等记录、bind_code 行锁、channel_identity 编写或扩展用例并记录 RED；关键断言：并发和重启后一次消费；租户/endpoint 隔离；响应重放；错误事务回滚；数据库保存 checksum 而非明文绑定码。执行 argv：`["uv","run","pytest","-q","tests/console_channel/test_channel_bind_idempotency.py","-k","b103"]`。
- [x] 实现或补齐：在 Console Owner 事务内复用现有幂等存储原语：同 key/同指纹重放首次响应，异指纹 `IDEMPOTENCY_MISMATCH`（与既有 skill/agent/mcp 幂等原语一致）；无同 key 重放时已用绑定码仍 BIND_CODE_INVALID；绑定不自动授予 Agent 权限。复用 control.skill_import_idempotency，endpoint 固定 /internal/channel/bind；已核实包含租户/key/endpoint partial unique、request_fingerprint 与 response_json，不新增迁移或 Gateway 业务表。
- [x] 执行上述契约命令，填写 Acceptance Evidence 的 RED/GREEN、每个关键断言位置、真实组件与清理记录；失败、skip 或外部阻塞保留未验证。所有代码改动有对应测试，函数≤50行，强类型与显式异常处理。

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| B-103 | integration | 真实 bind HTTP handler→PostgreSQL 幂等记录、bind_code 行锁、channel_identity | 并发和重启后一次消费；租户/endpoint 隔离；响应重放；错误事务回滚；数据库保存 checksum 而非明文绑定码 | tests/console_channel/test_channel_bind_idempotency.py / B-103（verified） | `["uv","run","pytest","-q","tests/console_channel/test_channel_bind_idempotency.py","-k","b103"]` | verified |

### Acceptance Evidence

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|---|---|---|---|---|---|
| B-103 | `uv run pytest -q tests/console_channel/test_channel_bind_idempotency.py -k b103` → `6 failed`：无幂等记录（`_fetch_idempotency` 返回 None）、异指纹未报 409、并发重复消费、失败事务未回滚语义可辨、租户/endpoint 未隔离 | 同一命令 → `6 passed` | `test_b103_replay_returns_first_response_without_second_consumption`（同 key 同指纹重放首次响应、identity 仍为 1、库里留有 response_json；换 key 复用已用码仍 BIND_CODE_INVALID）；`test_b103_same_key_different_fingerprint_conflicts`（409 IDEMPOTENCY_MISMATCH 且不为新用户建 identity）；`test_b103_concurrent_same_key_consumes_once`（`asyncio.gather` 两个同 key 请求 → 均 200、返回相同 platform_user_id、identity 数 1、绑定码仅 USED 一次）；`test_b103_failed_attempt_rolls_back_and_retry_with_same_key_succeeds`（无效码失败后无幂等记录，同 key 重试成功并落记录）；`test_b103_tenant_and_endpoint_are_isolated`（同 key 挂在其他 endpoint 不被重放；另一租户用同 key 独立处理，本租户重放结果不变）；`test_b103_storage_keeps_checksums_not_plaintext`（bind_code 只存 `sha256:` 哈希，指纹与 response_json 均不含明文绑定码） | 真实 FastAPI bind handler + 真实 PostgreSQL：幂等记录写 `control.skill_import_idempotency`（partial unique `(tenant_id, idempotency_key, endpoint)`）、`SELECT bind_code FOR UPDATE` 行锁、`pg_advisory_xact_lock` 串行化并发；断言直接查库（`SkillImportIdempotency`/`BindCode`/`ChannelIdentity`）与真实 HTTP 响应，无 mock | verified |

补充记录：
- 实现范围：`internal_channel.py` 的 `/bind` 接受 `Idempotency-Key`（`Annotated[str | None, Header(max_length=128)]`，与 skills/agents/mcp 端点同一 house 约定）；`channel_service.py` 的 `bind()` 增加幂等包装（`_lock_idempotency` / `_idempotency_replay` / `_record_idempotency` / `_find_idempotency`），原绑定逻辑抽为 `_bind_once` 保持行数约束；指纹 `bind_fingerprint()` 按 RULE-api-002 现行文本 = 规范化 JSON（sort_keys + 紧凑分隔符）SHA256，含 endpoint/tenant/channel/bot_id/external_user_id 与**绑定码 checksum**（不存明文）。
- 事务原子性复用既有 `get_session`（成功 commit / 异常 rollback），因此失败分支不留下成功响应；跨请求重放从 PG 读取（等价重启后重放）。
- 复用而非新建表：`control.skill_import_idempotency` 与 Console skill/agent/mcp 端点共用。
- 回归：`tests/console_channel` → `33 passed`；非验收全量 → `1144 passed`。
- 外部依赖：无（本任务不消费 Runtime/Worker；Gateway 侧传 key 属 TASK-010）。
- 清理：纯 DB 行 + ASGI 调用，无进程/Redis 副作用；测试租户数据由既有 conftest 守卫清理。
- B-103: verified — automated command passed; run_id=df28682ac59e4e57859ff540e21bc7ae (confirmed_by: runner)

### Log
- [2026-09-20] prepared (draft)
- [2026-09-21] 用户确认后写入；设计修订已承接，状态保持 draft。

---
- [2026-09-23] started
- [2026-09-23] completed (done)
## TASK-004: 补齐 Console Effective Skills 内部端点

- **Status**: done
- **Priority**: P0
- **Depends**: TASK-001
- **Source**: 10-im-gateway.backend.design.md#API-04 查询可用 Skills, 10-im-gateway.backend.design.md#2.5.1 业务规则与约束
- **Spec-Refs**: harness-data#RULE-data-001
- **Acceptance-Refs**: B-104, RULE-data-001
- **Files**: `apps/console-platform/backend/src/muad_console_platform/api/internal_channel.py`, `apps/console-platform/backend/src/muad_console_platform/application/channel_skills_service.py`, `tests/console_channel/test_channel_skills_api.py`
- **Estimate**: 15–60 分钟；超出先拆分

### Description

补当前缺失的 GET /internal/channel/skills，复用 Effective Capability；仅返回 name/platform_label/description 等允许字段。按 API-04 的统一分页契约输出，含 enabled/is_deleted 与 SELECTED 用户授权过滤，不复制一套授权公式。

### Checklist

- [x] [B-104][integration] 修改生产代码前先按 真实 Console handler→生产授权服务→PostgreSQL Agent/Skill/Grant 编写或扩展用例并记录 RED；关键断言：拒绝无 Agent 授权；禁用/删除/未授权 Skill 名称描述均不可见；分页边界与查询数量有界。执行 argv：`["uv","run","pytest","-q","tests/console_channel/test_channel_skills_api.py","-k","b104"]`。
- [x] 实现或补齐：补当前缺失的 GET /internal/channel/skills，复用 Effective Capability；仅返回 name/platform_label/description 等允许字段。按 API-04 的统一分页契约输出，含 enabled/is_deleted 与 SELECTED 用户授权过滤，不复制一套授权公式。
- [ ] [RULE-data-001][integration] verifier_ref=harness-data#RULE-data-001；原 verifier 输入 argv=`["uv","run","pytest","-q","tests","-k","schema_parity"]`；补充真实边界 真实 PostgreSQL 表结构与约束（标准列、`is_deleted=false` partial unique、跨 Schema 逻辑 UUID）；原 Spec verifier 真实边界，断言 本模块不新增表；复用 `control.skill_import_idempotency` / `control.bind_code` 等 Owner 表时沿用标准列与 partial unique；新查询不引入只藏在 JSON 的关键字段；原 verifier 全部通过，联合验收 argv=`["bash","-lc","uv run pytest -q tests/console_channel/test_channel_skills_api.py -k b104 && uv run pytest -q tests -k schema_parity"]`。
- [x] 执行上述契约命令，填写 Acceptance Evidence 的 RED/GREEN、每个关键断言位置、真实组件与清理记录；失败、skip 或外部阻塞保留未验证。所有代码改动有对应测试，函数≤50行，强类型与显式异常处理。

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| B-104 | integration | 真实 Console handler→生产授权服务→PostgreSQL Agent/Skill/Grant | 拒绝无 Agent 授权；禁用/删除/未授权 Skill 名称描述均不可见；分页边界与查询数量有界 | tests/console_channel/test_channel_skills_api.py / B-104（verified） | `["uv","run","pytest","-q","tests/console_channel/test_channel_skills_api.py","-k","b104"]` | verified |
| RULE-data-001 | integration | 真实 PostgreSQL 表结构与约束（标准列、`is_deleted=false` partial unique、跨 Schema 逻辑 UUID）；原 Spec verifier 真实边界 | 不新增表；复用 Owner 表沿用标准列与 partial unique；关键查询字段不藏在 JSON；原 verifier 全部通过 | tests/console_channel/test_channel_skills_api.py + 原 verifier / RULE-data-001（planned） | `["bash","-lc","uv run pytest -q tests/console_channel/test_channel_skills_api.py -k b104 && uv run pytest -q tests -k schema_parity"]` | planned |

### Acceptance Evidence

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|---|---|---|---|---|---|
| B-104 | `uv run pytest -q tests/console_channel/test_channel_skills_api.py -k b104` → `3 failed`（端点不存在：404 COMMON_NOT_FOUND，`data` 为 None） | 同一命令 → `3 passed` | `test_b104_returns_only_effective_catalog_with_allowed_fields`（可见集合恰为 ALL 与 SELECTED+Grant 两条；每条仅含 skill_id/key/name/platform_label/description；未授权/禁用/撤销/解绑 4 条的名称与 canary 描述、artifact frontmatter 全文均不出现在响应体）；`test_b104_rejects_user_without_agent_grant`（无 User→Agent Grant → 403 AGENT_ACCESS_DENIED，且不泄露任何 skill 名称）；`test_b104_pagination_is_bounded_and_validated`（page_size=1 分两页各 1 条且 `total=2`、越界页返回空窗口但 total 不变；page=0 / page_size=101 → 422 COMMON_VALIDATION_ERROR；SQL 事件监听断言存在 `count(` 与 `limit`，即有界查询、无 N+1） | 真实 FastAPI handler + 生产授权公式 + 真实 PostgreSQL：`AgentAccessGrantRepository.has_active_grant`（User→Agent）与 `SkillRepository.list_effective_for_agent`（AgentSkillBinding + current artifact join + `enabled`/`is_deleted` + `user_scope=ALL` 或 `SkillUserGrant`）；断言基于真实库中种子行与真实 SQL 语句 | verified |

补充记录：
- 实现范围：新增 `application/channel_skills_service.py`（`validate_page` → 授权 → `count` + `limit/offset` 有界查询 → 映射允许字段，`platform_label or name` 兜底）；`api/internal_channel.py` 新增 `GET /internal/channel/skills`（query 走 api-kit `validate_page`，越界码由 catalog 映射为 422）。
- 连带（复用而非复制授权公式）：`skill_repository.py` 抽出 `_effective_conditions()` 供 list/count 共用，并给 `list_effective_for_agent` 增加可选 `limit/offset`（默认取全部，`resolve_service` 行为不变）、新增 `count_effective_for_agent`。
- 不新增表、不新增迁移；未授权资源的名称/描述/存在性均不返回（canary 断言）。
- 回归：`tests/console_channel + tests/console_skill` → `98 passed`（含 resolve/snapshot 侧）；非验收全量 → `1147 passed`。
- 外部依赖：无（EXT-02/07 的 Console 授权事务已存在，本任务只暴露内部端点）。
- 清理：测试自种子数据在 fixture finally 中按 id 硬删除（skill_user_grant → agent_skill_binding → skill_artifact → skill），不阻塞共享 conftest 的租户清理；无进程/Redis 副作用。
- B-104: verified — automated command passed; run_id=56275c0380914901b215c4cc1d2157e8 (confirmed_by: runner)

### Log
- [2026-09-20] prepared (draft)
- [2026-09-21] 用户确认后写入；设计修订已承接，状态保持 draft。

---
- [2026-09-23] started
- [2026-09-23] resumed (in-progress)
- [2026-09-23] completed (done)
## TASK-005: 补 Bot 快照轮询与热更新边界

- **Status**: done
- **Priority**: P0
- **Depends**: TASK-001, TASK-002
- **Source**: 10-im-gateway.backend.design.md#3.2.2 Bot 快照轮询与 Secret 解析, 10-im-gateway.backend.design.md#API-01 Bot 列表
- **Spec-Refs**: 
- **Acceptance-Refs**: B-105
- **Files**: `apps/im-gateway/src/muad_im_gateway/application/bot_snapshot.py`, `apps/console-platform/backend/src/muad_console_platform/api/internal_channel.py`, `tests/gateway/test_bot_snapshot.py`
- **Estimate**: 15–60 分钟；超出先拆分

### Description

保留启动全量、30s 轮询与 revision 更新；按 API-01 补快照分页，若 revision 跨页变化丢弃不完整快照并在下次节拍重拉；依赖故障保留最近完整快照，新增/停用/换 Agent/换 secret 只更新相关 bot。

### Checklist

- [x] [B-105][integration] 修改生产代码前先按 Console snapshot HTTP→真实 PG bot 配置→BotSnapshotCache 编写或扩展用例并记录 RED；关键断言：revision 不变不重连；失败不清空；跨页版本不混合；原始 secret 只驻内存且不进入日志。执行 argv：`["uv","run","pytest","-q","tests/gateway/test_bot_snapshot.py","-k","b105"]`。
- [x] 实现或补齐：保留启动全量、30s 轮询与 revision 更新；按 API-01 补快照分页，若 revision 跨页变化丢弃不完整快照并在下次节拍重拉；依赖故障保留最近完整快照，新增/停用/换 Agent/换 secret 只更新相关 bot。
- [x] 执行上述契约命令，填写 Acceptance Evidence 的 RED/GREEN、每个关键断言位置、真实组件与清理记录；失败、skip 或外部阻塞保留未验证。所有代码改动有对应测试，函数≤50行，强类型与显式异常处理。

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| B-105 | integration | Console snapshot HTTP→真实 PG bot 配置→BotSnapshotCache | revision 不变不重连；失败不清空；跨页版本不混合；原始 secret 只驻内存且不进入日志 | tests/gateway/test_bot_snapshot.py / B-105（planned） | `["uv","run","pytest","-q","tests/gateway/test_bot_snapshot.py","-k","b105"]` | verified |

### Acceptance Evidence

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|---|---|---|---|---|---|
| B-105 | **RED 补记（据实说明：三条用例在实现完成后编写，非测试先行）**：回退 5 个生产文件、保留测试后运行 `uv run pytest -q tests/gateway/test_bot_snapshot.py -k b105` → `1 failed, 2 passed`；失败项 `test_b105_multi_page_collection_is_bounded_and_consistent`（旧实现不按 total 翻页，缺少第 2 页的 `bot-c`）。另两条在旧实现下即通过（只覆盖单页成功与失败保留），故其 GREEN **不单独构成新行为证据** | 恢复生产改动后同一命令 → `3 passed` | `test_b105_collects_real_console_snapshot_and_reuses_revision`（真实 Console 进程 + 真实 PG：只返回启用 bot、revision 匹配 `^sha256:[0-9a-f]{64}$`、revision 不变不再发布、轮换 secret 后 revision 变化并重新发布、日志中无任何 secret canary 且 revision 不含 secret）；`test_b105_multi_page_collection_is_bounded_and_consistent`（page_size=2/total=3 → 恰好请求 page 1、2 并合并 3 条；第 2 页 revision 不一致 → 本次读取丢弃、旧快照与旧 revision 保留且不发布；下一节拍一致后发布新 revision）；`test_b105_console_failure_keeps_previous_snapshot`（500 → items/revision 保留、`console_reachable=False`、不发布） | 真实 Console 以**独立进程**启动（`uvicorn muad_console_platform.main:app`，真实 socket + 真实 PostgreSQL bot 配置，与验收栈同一 env 口径：DATABASE_URL/ARTIFACT_ROOT/SKILL_CACHE_ROOT）；跨页不一致与 Console 500 两条防御路径用本地脚本化 HTTP 服务（真实 HTTP，Gateway 侧逻辑为目标）——真实 Console 无法在中途制造跨页 revision 漂移 | verified |

补充记录：
- 实现范围（Console 侧）：`bot_account_repository` 新增 `list_enabled_page`（稳定排序 + limit/offset）与 `enabled_snapshot_digest`（单次聚合查询给出 total 与覆盖全部启用 bot 的摘要：逐行 `hashtextextended` 求和后 sha256，与行序无关，不暴露明文；PG 无 `string_agg(... ORDER BY ...)` 的 SQLAlchemy 有序聚合渲染，故选择与行序无关的摘要）；`channel_service.bots` 改为分页 + `validate_page`；`/internal/channel/bots` 增加 page/page_size（越界 → 422 COMMON_VALIDATION_ERROR）。
- 实现范围（Gateway 侧）：`ConsoleClient.bots(page, page_size)`；`BotSnapshotCache._collect_snapshot()` 按 `total/page_size` 有界翻页（上界 `BOT_SNAPSHOT_MAX_PAGES=100`），跨页 `revision`/`total` 不一致即丢弃本次读取并保留旧快照到下个节拍；依赖失败仍保留最近完整快照（不清空）；revision 不变不触发回调（不重连）；日志只记录 code/revision，不落 secret。
- 连带（类型跟随）：`tests/gateway/fakes.py` 的 `FakeConsoleClient.bots` 接受 page/page_size；`tests/console_channel/test_channel_bots_api.py` 三条既有用例在分页后仍通过（断言基于 revision/items，未固定键集合）。
- 回归：`tests/gateway + tests/console_channel` → `186 passed`；非验收全量 → `1150 passed`。
- 外部依赖：无（EXT-02/07 的 Console 快照端点已存在，本任务补齐分页与 Gateway 侧读取语义）。
- 清理：Console 子进程在 finally 中 terminate/kill 并等待退出；测试 bot/agent/model 行按 tenant 硬删除；运行后 `ps` 无残留进程。
- B-105: verified — automated command passed; run_id=4304fca5b5ae45dc8599ef447bacd08f (confirmed_by: runner)
- B-105: verified — automated command passed; run_id=53097ef57a3e4c1fbc9a3c1fde197cbb (confirmed_by: runner)

### Log
- [2026-09-20] prepared (draft)
- [2026-09-21] 用户确认后写入；设计修订已承接，状态保持 draft。

---
- [2026-09-23] started
- [2026-09-23] completed (done)
## TASK-006: 修复多 Bot 故障隔离与 WS 退避

- **Status**: draft
- **Priority**: P0
- **Depends**: TASK-005, TASK-021, TASK-031
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

## TASK-008: 对齐入站 Redis 原子去重与降级

- **Status**: done
- **Priority**: P0
- **Depends**: TASK-001
- **Source**: 10-im-gateway.backend.design.md#3.2.3 入站去重
- **Spec-Refs**: 
- **Acceptance-Refs**: B-108, S-05, RULE-08
- **Files**: `apps/im-gateway/src/muad_im_gateway/infrastructure/dedupe.py`, `apps/im-gateway/src/muad_im_gateway/application/inbound.py`, `tests/gateway/test_inbound_dedupe_integration.py`
- **Estimate**: 15–60 分钟；超出先拆分

### Description

沿用 SET NX EX 600，在解析、resolve 与创建 Run 前判重；用真实 Redis 覆盖并发、TTL 和恢复，Redis 断线继续 at-least-once，不以进程内 set 冒充跨副本去重。

### Checklist

- [x] [B-108][integration] 修改生产代码前先按 Gateway inbound→真实 Redis→真实 Runtime HTTP 接收边界 编写或扩展用例并记录 RED；关键断言：相同 channel/message_id 只首次下游调用；重复 ACK/忽略；TTL=600；Redis 故障继续处理；message_id 不变。执行 argv：`["uv","run","pytest","-q","tests/gateway/test_inbound_dedupe_integration.py","-k","b108"]`。
- [x] [S-05][integration] 修改生产代码前先按 Gateway入站→真实Redis dedupe→真实下游HTTP观测 编写或扩展用例并记录 RED；关键断言：SET NX EX600；第二次ACK/忽略且不创建第二Run。执行 argv：`["uv","run","pytest","-q","tests/gateway/test_inbound_dedupe_integration.py","-k","s05"]`。
- [x] 实现或补齐：沿用 SET NX EX 600，在解析、resolve 与创建 Run 前判重；用真实 Redis 覆盖并发、TTL 和恢复，Redis 断线继续 at-least-once，不以进程内 set 冒充跨副本去重。
- [x] [RULE-08][integration] 作为唯一最终负责人，沿 Gateway inbound→真实 Redis→真实 Runtime HTTP 接收边界 验证 入站Redis SET NX EX600、降级at-least-once；联合映射 S-05 / E-06；命令 `["uv","run","pytest","-q","tests/gateway/test_inbound_dedupe_integration.py","-k","b108"]`，不得以任务标题或静态声明代替行为证据。
- [x] 执行上述契约命令，填写 Acceptance Evidence 的 RED/GREEN、每个关键断言位置、真实组件与清理记录；失败、skip 或外部阻塞保留未验证。所有代码改动有对应测试，函数≤50行，强类型与显式异常处理。

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| B-108 | integration | Gateway inbound→真实 Redis→真实 Runtime HTTP 接收边界 | 相同 channel/message_id 只首次下游调用；重复 ACK/忽略；TTL=600；Redis 故障继续处理；message_id 不变 | tests/gateway/test_inbound_dedupe_integration.py / B-108（verified） | `["uv","run","pytest","-q","tests/gateway/test_inbound_dedupe_integration.py","-k","b108"]` | verified |
| S-05 | integration | Gateway入站→真实Redis dedupe→真实下游HTTP观测 | SET NX EX600；第二次ACK/忽略且不创建第二Run | tests/gateway/test_inbound_dedupe_integration.py / S-05（verified） | `["uv","run","pytest","-q","tests/gateway/test_inbound_dedupe_integration.py","-k","s05"]` | verified |
| RULE-08 | integration | Gateway inbound→真实 Redis→真实 Runtime HTTP 接收边界 | 入站Redis SET NX EX600、降级at-least-once；联合映射 S-05 / E-06 | tests/gateway/test_inbound_dedupe_integration.py / RULE-08（verified） | `["uv","run","pytest","-q","tests/gateway/test_inbound_dedupe_integration.py","-k","b108"]` | verified |

### Acceptance Evidence

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|---|---|---|---|---|---|
| B-108 | **无 RED（据实说明）**：入站去重生产行为已符合设计（`handle()` 先 `_mark_seen()`；key `im:dedupe:{channel}:{message_id}`；`SET NX EX 600`；`DedupeStoreError` 降级继续；重复忽略），按 Baseline「已有正确行为先跑回归，不人为制造失败」不伪造 RED。**用例有效性由反证用例保证**：`test_b108_negative_control_without_store_duplicates_pass_through`（去重关闭时同一 message_id 确实重复下游调用 → 上界断言非空断言） | `uv run pytest -q tests/gateway/test_inbound_dedupe_integration.py -k b108` → `4 passed`（含反证） | `test_b108_duplicate_message_is_ignored_with_single_downstream_call`（首次下游 1 次且 `message.id` 原样透传；重复投递后仍为 1 次；Redis 值 `1`、TTL ≤ 600）；`test_b108_concurrent_same_message_id_calls_downstream_once`（同一 message_id 并发两次 → 下游恰好 1 次）；`test_b108_redis_failure_degrades_to_at_least_once`（不可达 Redis → 仍处理且 id 不变）；`test_b108_negative_control_*`（反证） | 真实 Redis（`build_dedupe_store(REDIS_URL)`；非 Redis 实例直接失败，不 skip）+ 真实 Runtime HTTP 接收端（`uvicorn` 线程真实 socket，观测 `POST /v1/runs` 次数与请求体，返回真实 SSE 流） | verified |
| S-05 | 同上（回归性验收，无 RED） | `-k s05` → `1 passed` | `test_s05_set_nx_ex600_and_second_delivery_creates_no_second_run`（`set_if_absent` 首次 True/二次 False；真实 TTL 595–600；已存在时命中被忽略，下游 0 次即不创建第二个 Run） | 同上（真实 Redis + 真实 Runtime 接收端） | verified |
| RULE-08 | 同上（回归性验收，无 RED） | `-k b108` → `4 passed`（与 B-108 共用命令，同一次执行须同时满足两条义务） | 唯一最终负责人：入站 `SET NX EX 600` 与降级 at-least-once 的行为证据即上述 B-108 断言 + `test_b108_redis_failure_degrades_to_at_least_once`；联合映射 S-05 / E-06（E-06 的投递侧在 TASK-027） | 真实 Redis 原子语义 + 真实 Runtime HTTP 接收端 | verified |

补充记录：
- 实现范围：**本任务未改生产代码** —— 现状已满足 §3.2.3（判重在命令路由/resolve/Run 之前；TTL 常量 `DEDUPE_TTL_SEC = 600`；`is_duplicate` = `SET NX`；`DedupeStoreError` → warn 并继续）。新增验收测试 `tests/gateway/test_inbound_dedupe_integration.py` 锁定该行为。
- 反证设计：去重关闭（`NullDedupeStore`）时重复投递会重复调用下游，证明上界断言有效；若未来有人把跨副本去重退回进程内实现，b108 用例与其反证会同时暴露。
- 回归：`tests/gateway` → `155 passed`；非验收全量 → `1155 passed`。
- 外部依赖：无（设计与 RULE-08 不依赖 Runtime 真实 Run 语义，接收端只需承载 HTTP/SSE 观测）。
- 清理：用例结束 `DEL im:dedupe:WECOM:<message_id>`（`_cleanup_keys`），uvicorn 线程 fixture finally 退出；无残留键/进程。
- B-108: verified — automated command passed; run_id=e4104cb8c70a4d7b931dd49f51a95eaa (confirmed_by: runner)
- S-05: verified — automated command passed; run_id=e4104cb8c70a4d7b931dd49f51a95eaa (confirmed_by: runner)

### Log
- [2026-09-20] prepared (draft)
- [2026-09-21] 用户确认后写入；设计修订已承接，状态保持 draft。

---
- [2026-09-23] started
- [2026-09-23] completed (done)
## TASK-009: 对齐 resolve、未绑定与授权分流

- **Status**: done
- **Priority**: P0
- **Depends**: TASK-001, TASK-002
- **Source**: 10-im-gateway.backend.design.md#API-02 解析消息路由, 10-im-gateway.backend.design.md#API-06 Runtime Run 桥接
- **Spec-Refs**: 
- **Acceptance-Refs**: B-109, E-01
- **Files**: `apps/im-gateway/src/muad_im_gateway/application/inbound.py`, `tests/gateway/test_message_routing_integration.py`
- **Estimate**: 15–60 分钟；超出先拆分
- **External-Depends**: EXT-08（真实 Runtime Run/授权契约与对应验收 Evidence）

### Description

透传 conversation 标识，按 bound/authorized 明确分流；未绑定给绑定提示，禁用/不存在 bot 使用 BOT_NOT_FOUND；只传 logical agent_id，不保存 Agent→Pod 或 active run 映射。

### Checklist

- [x] [B-109][integration] 修改生产代码前先按 Gateway→真实 Console resolve HTTP→PostgreSQL→Runtime 接收观测 编写或扩展用例并记录 RED；关键断言：bound=false 为正常分支；未绑定/无授权/禁用 bot 无 Run；同用户跨 bot 的 route 不串线。执行 argv：`["uv","run","pytest","-q","tests/gateway/test_message_routing_integration.py","-k","b109"]`。
- [x] [E-01][integration] 修改生产代码前先按 Gateway→真实bot resolve HTTP→PostgreSQL 编写或扩展用例并记录 RED；关键断言：bot未知/禁用返回BOT_NOT_FOUND；不创建Run。执行 argv：`["uv","run","pytest","-q","tests/gateway/test_message_routing_integration.py","-k","e01"]`。
- [x] 实现或补齐：透传 conversation 标识，按 bound/authorized 明确分流；未绑定给绑定提示，禁用/不存在 bot 使用 BOT_NOT_FOUND；只传 logical agent_id，不保存 Agent→Pod 或 active run 映射。
- [x] 执行上述契约命令，填写 Acceptance Evidence 的 RED/GREEN、每个关键断言位置、真实组件与清理记录；失败、skip 或外部阻塞保留未验证。所有代码改动有对应测试，函数≤50行，强类型与显式异常处理。

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| B-109 | integration | Gateway→真实 Console resolve HTTP→PostgreSQL→Runtime 接收观测 | bound=false 为正常分支；未绑定/无授权/禁用 bot 无 Run；同用户跨 bot 的 route 不串线 | tests/gateway/test_message_routing_integration.py / B-109（verified） | `["uv","run","pytest","-q","tests/gateway/test_message_routing_integration.py","-k","b109"]` | verified |
| E-01 | integration | Gateway→真实bot resolve HTTP→PostgreSQL | bot未知/禁用返回BOT_NOT_FOUND；不创建Run | tests/gateway/test_message_routing_integration.py / E-01（verified） | `["uv","run","pytest","-q","tests/gateway/test_message_routing_integration.py","-k","e01"]` | verified |

### Acceptance Evidence

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|---|---|---|---|---|---|
| E-01 | `uv run pytest -q tests/gateway/test_message_routing_integration.py` → `1 failed, 2 passed`：`test_e01_unknown_disabled_and_deleted_bot_return_bot_not_found` 失败——禁用/删除/未知 bot 由 Console 返回 **200 `bound:false`**，而设计 E-01/API-02 要求 `BOT_NOT_FOUND`（Gateway 因此无法区分"未绑定用户"与"bot 不可用"） | 同一命令 → `3 passed` | `test_e01_unknown_disabled_and_deleted_bot_return_bot_not_found`（未知/禁用/删除三类 bot 均 `BOT_NOT_FOUND`，状态码 403/404 由 catalog 映射；真实 Console HTTP + 真实 PG 查询 `bot_account` 的 enabled/is_deleted） | 真实 Console 服务**独立进程**（uvicorn + 真实 socket）+ 真实 PostgreSQL 的 bot/identity/grant 数据（复用 console_channel 真实夹具，经 sys.path 显式导入） | verified |
| B-109 | 同上（`bound=false` 正常分支与 route 不串线在现状即通过，属回归性断言） | `-k b109` → `2 passed` | `test_b109_unbound_and_unauthorized_are_normal_branches_without_run`（未绑定用户 → `bound=false` 正常分支，回绑定提示且 **Runtime 未收到任何 Run**；已绑定但无 `AgentAccessGrant` → 拒绝访问且不创建 Run）；`test_b109_authorized_message_creates_run_with_matching_route`（授权消息 → Runtime 恰好收到 1 次：`message.id` 原样、`channel.bot_id` 与发送 bot 一致（route 不串线）、`channel.external_conversation_id` 透传、`agent_id` 为逻辑 Agent、payload 不含 pod；且 **resolve 出站请求携带同一会话标识**——由 `RecordingConsoleClient` 记录出站 payload，仍走真实 HTTP） | 真实 Console 进程 + 真实 PostgreSQL + 真实 Runtime HTTP 接收端（uvicorn 真实 socket，记录是否创建 Run 与请求体） | verified |

补充记录：
- 实现范围：①Console `channel_service.resolve`：`find_enabled` 为 None（未知/禁用/已删除）时改为 `raise AppError(ErrorCode.BOT_NOT_FOUND)`，与设计 API-02 处理口径一致；②Gateway `inbound._resolve`：`ChannelResolveRequest` 补 `external_conversation_id=envelope.external_conversation_id`（设计 API-02 请求字段 + TASK-009「透传 conversation 标识」）。
- 连带（语义变更同步，显式登记）：`tests/console_channel/test_channel_resolve_api.py` 中 3 条既有用例（unknown/disabled/deleted → 原断言 `bound:false`）改为断言 `BOT_NOT_FOUND`；`test_tenant_isolation_hides_other_tenant_bot` 由「其他租户 bot 返回 bound:false」改为 `BOT_NOT_FOUND`（跨租户视角等同未配置，且不泄露存在性——比原断言更强的隔离）。
- 回归：`tests/console_channel` → `36 passed`；`tests/gateway` → `158 passed`；非验收全量 → `1158 passed`。
- 外部依赖：无（仅 Console 契约 + Runtime 接收观测）。
- 清理：Console 子进程 fixture finally terminate/kill；Runtime 接收端线程 `should_exit` + join；真实 PG 数据由 console_channel 夹具自清理。
- B-109: verified — automated command passed; run_id=b0881999e65645a3b1dc74aefff9610a (confirmed_by: runner)
- E-01: verified — automated command passed; run_id=b0881999e65645a3b1dc74aefff9610a (confirmed_by: runner)

### Log
- [2026-09-20] prepared (draft)
- [2026-09-21] 用户确认后写入；设计修订已承接，状态保持 draft。

---
- [2026-09-23] started
- [2026-09-23] completed (done)
## TASK-010: 完善 /bind 命令与稳定幂等键

- **Status**: done
- **Priority**: P0
- **Depends**: TASK-002, TASK-003
- **Source**: 10-im-gateway.backend.design.md#3.4.2 内置命令与文案映射（FEAT-02）, 10-im-gateway.backend.design.md#API-03 执行绑定
- **Spec-Refs**: 
- **Acceptance-Refs**: B-110
- **Files**: `apps/im-gateway/src/muad_im_gateway/application/inbound.py`, `apps/im-gateway/src/muad_im_gateway/application/console_client.py`, `tests/gateway/test_bind_command.py`
- **Estimate**: 15–60 分钟；超出先拆分

### Description

本地识别 /bind 与缺参数提示，不发送 LLM；将原 channel message_id 作为稳定 Idempotency-Key 传 Console；成功、无效、过期与已消费分支使用设计文案和 error catalog。

### Checklist

- [x] [B-110][integration] 修改生产代码前先按 Gateway command→真实 Console bind HTTP→PostgreSQL 编写或扩展用例并记录 RED；关键断言：绑定成功回复且身份可回读；错误码准确；重投消息不重新消费；bind code 不出日志/Runtime 请求。执行 argv：`["uv","run","pytest","-q","tests/gateway/test_bind_command.py","-k","b110"]`。
- [x] 实现或补齐：本地识别 /bind 与缺参数提示，不发送 LLM；将原 channel message_id 作为稳定 Idempotency-Key 传 Console；成功、无效、过期与已消费分支使用设计文案和 error catalog。
- [x] 执行上述契约命令，填写 Acceptance Evidence 的 RED/GREEN、每个关键断言位置、真实组件与清理记录；失败、skip 或外部阻塞保留未验证。所有代码改动有对应测试，函数≤50行，强类型与显式异常处理。

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| B-110 | integration | Gateway command→真实 Console bind HTTP→PostgreSQL | 绑定成功回复且身份可回读；错误码准确；重投消息不重新消费；bind code 不出日志/Runtime 请求 | tests/gateway/test_bind_command.py / B-110（verified） | `["uv","run","pytest","-q","tests/gateway/test_bind_command.py","-k","b110"]` | verified |

### Acceptance Evidence

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|---|---|---|---|---|---|
| B-110 | `uv run pytest -q tests/gateway/test_bind_command.py -k b110` → `1 failed, 2 passed`：`test_b110_replayed_message_does_not_consume_twice` 失败——Gateway 未传 `Idempotency-Key`，Console 无法重放，同 message_id 重投被按"已用码"拒绝（`BIND_CODE_INVALID`） | 同一命令 → `3 passed` | `test_b110_bind_success_replies_and_persists_identity`（成功回复 `BIND_SUCCESS_TEXT`；真实 PG 中 identity 数=1、bind_code `USED`；`/bind` 不调用 Runtime；caplog 中断言**绑定码不出现在日志**）；`test_b110_replayed_message_does_not_consume_twice`（同 message_id 重投两次均成功回复，identity 仍为 1，`used_at` 与首次一致 = 未二次消费）；`test_b110_error_branches_use_catalog_texts`（缺参数 → 本地 `BIND_USAGE_TEXT` 且不发请求；未知码 → `BIND_CODE_INVALID` 文案；过期码 → `BIND_CODE_EXPIRED` 文案；已用码（不同 message_id）→ `BIND_CODE_INVALID` 文案；全程 Runtime 未被调用） | 真实 Console 服务**独立进程**（uvicorn + 真实 socket）+ 真实 PostgreSQL（bind_code 状态与 channel_identity 直接查库断言）+ 真实 HTTP `Idempotency-Key=原 channel message_id`（Console 侧持久幂等由 TASK-003 落地）；Runtime 侧用 Fake（不在 B-110 声明的边界内）但断言其零调用 | verified |

补充记录：
- 实现范围：`ConsoleClient.bind(..., idempotency_key=None)` 增加可选 `Idempotency-Key` 出站头（`_idempotency_headers`，空则不带头）；`inbound._handle_bind` 传 `idempotency_key=envelope.message_id`（设计 API-03「Gateway 传稳定 Idempotency-Key=channel message_id」）。
- 连带（去重与类型跟随，显式登记）：`tests/gateway/fakes.py` 的 `FakeConsoleClient.bind` 接受 `idempotency_key` 并记录 `bind_keys`；把真实 Console 子进程 helper 从 `test_bot_snapshot.py` / `test_message_routing_integration.py` 收敛到 `fakes.ConsoleProcess` 共用（消除三处重复，两套既有用例回归通过）。
- 回归：`tests/gateway + tests/console_channel` → `197 passed`；非验收全量 → `1161 passed`。
- 外部依赖：无（Console 幂等端点已由 TASK-003 落地）。
- 清理：Console 子进程 fixture finally terminate/kill；真实 PG 数据由 console_channel 夹具自清理。
- B-110: verified — automated command passed; run_id=90af5b6195734dfe99fea5dd76a21abd (confirmed_by: runner)

### Log
- [2026-09-20] prepared (draft)
- [2026-09-21] 用户确认后写入；设计修订已承接，状态保持 draft。

---
- [2026-09-23] started
- [2026-09-23] completed (done)
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
- **Depends**: TASK-001
- **Source**: 10-im-gateway.backend.design.md#API-06 Runtime Run 桥接, 10-im-gateway.backend.design.md#3.4.2 内置命令与文案映射（FEAT-02）
- **Spec-Refs**: 
- **Acceptance-Refs**: B-113
- **Files**: `apps/im-gateway/src/muad_im_gateway/application/runtime_client.py`, `tests/gateway/test_runtime_client.py`
- **Estimate**: 15–60 分钟；超出先拆分
- **External-Depends**: EXT-08（真实 Runtime Run/SSE 契约与幂等 Error Code 对齐证据）

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
- **Depends**: TASK-014, TASK-020
- **Source**: 10-im-gateway.backend.design.md#3.4.1 Runtime SSE 事件处理（FEAT-03）, 10-im-gateway.backend.design.md#3.4.2 内置命令与文案映射（FEAT-02）
- **Spec-Refs**: 
- **Acceptance-Refs**: B-115
- **Files**: `apps/im-gateway/src/muad_im_gateway/application/inbound.py`, `apps/im-gateway/src/muad_im_gateway/application/stream_renderer.py`, `tests/gateway/test_stream_renderer.py`
- **Estimate**: 15–60 分钟；超出先拆分
- **External-Depends**: EXT-08（真实 Runtime SSE 事件契约与终态语义证据）

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
- **External-Depends**: EXT-08（真实 Runtime Run 并发/自动 resume 契约与对应验收 Evidence）

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
- **External-Depends**: EXT-09-020 / EXT-09-021 / EXT-09-043（**已满足**，2026-09-23 复核：09-task-schedule 归档版 TASK-020/021/043 均 verified，实现已在 `api/delivery.py`、`infrastructure/dedupe.py` 落地，证据见 09 归档文件的 Acceptance Coverage 与 commit `50d5edc`）。本任务不重复实现原子去重与失败恢复，只做响应契约对齐。

### Description

对齐 accepted/deduplicated 响应与渠道错误映射；原子去重、发送失败恢复与 Redis 降级复用 EXT-09-021 已落地的实现，本任务不重复实现；未配置/禁用 bot 使用 BOT_NOT_FOUND，非法 route/message 使用 COMMON_VALIDATION_ERROR；artifact_ids 不转换为 IM 下载入口。新增用例沿用文件内既有夹具，不与既有 `test_b121_*` / `test_e05_*`（09 场景 ID）语义混用，新用例统一以 b117 前缀命名。

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
- **Files**: `packages/api-kit/src/muad_api/metrics.py`, `apps/im-gateway/src/muad_im_gateway/channels/wecom/adapter.py`, `tests/gateway/test_connection_observability.py`
- **Estimate**: 半天级（含设计 §4.2 的指标导出基建落地：api-kit 注册表 + `/metrics` 接线）；超出先拆基建与连接指标两项。

### Description

先落地指标导出基建（design v1.3 §4.2 决策）：`packages/api-kit` 进程内指标注册表，并在 Gateway（`main.py`）暴露真实 HTTP `GET /metrics`（Prometheus 文本格式，不引入新依赖）；再导出 wecom_ws_connected 与状态转换日志，记录 bot_id/from/to/attempt/trace；日志参数不得包含 SDK 原始异常密钥，标签不含消息正文。本任务只覆盖连接类指标，消息/去重/投递指标见 TASK-019。

### Checklist

- [ ] [B-118][integration] 修改生产代码前先按 真实连接迁移→生产日志 + 真实 `/metrics` HTTP 端点（api-kit 注册表） 编写或扩展用例并记录 RED；关键断言：连通/退避/停止指标随状态变化；坏 bot 不影响其他序列；日志字段完整且无 secret canary。执行 argv：`["uv","run","pytest","-q","tests/gateway/test_connection_observability.py","-k","b118"]`。
- [ ] 实现或补齐：通过现有可观测设施导出 wecom_ws_connected 与状态转换日志，记录 bot_id/from/to/attempt/trace；日志参数不得包含 SDK 原始异常密钥，标签不含消息正文。
- [ ] 执行上述契约命令，填写 Acceptance Evidence 的 RED/GREEN、每个关键断言位置、真实组件与清理记录；失败、skip 或外部阻塞保留未验证。所有代码改动有对应测试，函数≤50行，强类型与显式异常处理。

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| B-118 | integration | 真实连接迁移→生产日志 + 真实 `/metrics` HTTP 端点（api-kit 注册表） | 连通/退避/停止指标随状态变化；坏 bot 不影响其他序列；日志字段完整且无 secret canary | tests/gateway/test_connection_observability.py / B-118（planned） | `["uv","run","pytest","-q","tests/gateway/test_connection_observability.py","-k","b118"]` | planned |

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
- **Estimate**: 半天级（8 个指标 + 真实 SSE 观测接线；基建由 TASK-018 建立）；超出先拆入站/投递与流式两组。

### Description

装配 im_messages_total、im_runtime_errors_total、im_stream_first_chunk_ms、im_dedupe_hits_total、im_background_delivery_total、im_runtime_request_latency_ms、im_stream_latency_ms、im_message_failures_total；经 api-kit 注册表与 `/metrics` 端点导出（基建由 TASK-018 建立，本任务不重复建）；性能阈值保持待实测。标签以 docs/09 §6.4 为准，不含 Secret/正文。

### Checklist

- [ ] [B-119][integration] 修改生产代码前先按 生产入站/HTTP投递/真实SSE→真实 `/metrics` HTTP 端点（api-kit 注册表） 编写或扩展用例并记录 RED；关键断言：成功/失败/重复分支计数准确；首块和全流时延分开；无 secret/正文标签；同trace可关联。执行 argv：`["uv","run","pytest","-q","tests/gateway/test_message_metrics.py","-k","b119"]`。
- [ ] 实现或补齐：装配 im_messages_total、im_runtime_errors_total、im_stream_first_chunk_ms、im_dedupe_hits_total、im_background_delivery_total、im_runtime_request_latency_ms、im_stream_latency_ms、im_message_failures_total；性能阈值保持待实测。
- [ ] 执行上述契约命令，填写 Acceptance Evidence 的 RED/GREEN、每个关键断言位置、真实组件与清理记录；失败、skip 或外部阻塞保留未验证。所有代码改动有对应测试，函数≤50行，强类型与显式异常处理。

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| B-119 | integration | 生产入站/HTTP投递/真实SSE→真实 `/metrics` HTTP 端点（api-kit 注册表） | 成功/失败/重复分支计数准确；首块和全流时延分开；无 secret/正文标签；同trace可关联 | tests/gateway/test_message_metrics.py / B-119（planned） | `["uv","run","pytest","-q","tests/gateway/test_message_metrics.py","-k","b119"]` | planned |

### Acceptance Evidence
> planned。编码期填 RED/GREEN 命令与结果、断言路径/用例/位置、真实组件证据、外部依赖状态与清理证据；全部 verified 才可 done。本次结构检查不代表功能测试通过。

### Log
- [2026-09-20] prepared (draft)
- [2026-09-21] 用户确认后写入；设计修订已承接，状态保持 draft。

---

## TASK-020: 建立真实 WS 探针核心与官方 SDK 边界

- **Status**: done
- **Priority**: P0
- **Depends**: 
- **Source**: 10-im-gateway.backend.design.md#2.5.2 功能验收场景, 10-im-gateway.backend.design.md#3.1 技术选型与关键决策
- **Spec-Refs**: 
- **Acceptance-Refs**: B-120
- **Files**: `tests/e2e/wecom_probe_app.py`, `tests/acceptance/im_gateway/test_wecom_boundary.py`
- **Estimate**: 半天级（探针核心：真实 WS 服务骨架 + 官方 SDK 接线；故障注入已拆到 TASK-031）。若仍超 4 小时，按「认证握手 / 消息收发 / 流式回复」三段再拆。

### Description

建立本地真实 WebSocket 协议探针：官方 SDK 与生产 Adapter 经真实 socket 收发认证、消息与流式回复；替代外部第三方端点，不替代生产 Adapter/SDK。断线/握手拒绝/发送失败注入由 TASK-031 承担，本任务只保证核心收发真实可用。

### Checklist

- [x] [B-120][integration] 修改生产代码前先按 官方 SDK→真实本地 WebSocket 服务→生产 WeComAdapter 编写或扩展用例并记录 RED；关键断言：协议帧可回读；认证握手、消息收发与流式结束均经真实 socket；禁止 FakeChannelAdapter/MockTransport；探针不宣称企业微信实网验收。执行 argv：`["uv","run","pytest","-q","tests/acceptance/im_gateway/test_wecom_boundary.py","-k","b120"]`。
- [x] 实现或补齐：建立本地真实 WebSocket 协议探针，官方 SDK 与生产 Adapter 经真实 socket 收发认证/消息/流式回复；替代外部第三方端点，不替代生产 Adapter/SDK。
- [x] 执行上述契约命令，填写 Acceptance Evidence 的 RED/GREEN、每个关键断言位置、真实组件与清理记录；失败、skip 或外部阻塞保留未验证。所有代码改动有对应测试，函数≤50行，强类型与显式异常处理。

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| B-120 | integration | 官方 SDK→真实本地 WebSocket 服务→生产 WeComAdapter（探针核心：认证/消息/流式收发） | 协议帧可回读；认证握手、消息收发与流式结束均经真实 socket；禁止 FakeChannelAdapter/MockTransport；探针不宣称企业微信实网验收 | tests/acceptance/im_gateway/test_wecom_boundary.py / B-120（verified） | `["uv","run","pytest","-q","tests/acceptance/im_gateway/test_wecom_boundary.py","-k","b120"]` | verified |

### Acceptance Evidence

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|---|---|---|---|---|---|
| B-120 | **首次运行失败链（据实记录，本任务实现物是探针与用例，生产 Adapter/SDK 已存在）**：`uv run pytest -q tests/acceptance/im_gateway/test_wecom_boundary.py -k b120` 依次暴露 4 个真实问题并逐个修好：①`ValueError: ssl argument is incompatible with a ws:// URI`（官方 SDK 硬传 `ssl=`，探针必须 `wss://`）→ 改为真实 TLS + 自签证书；②连接态停留在 `CONNECTING`（认证是异步的，需等待）→ 加 `_wait_connected`；③`AttributeError: 'ServerConnection' object has no attribute 'closed'`（新 websockets API）→ 推送改为按发送结果剔除死连接；④流式无输出（Adapter 缓冲，需 `finish_stream`）→ 用例驱动收尾 flush | 修完后同一命令 → `4 passed` | `test_b120_authenticates_over_real_socket`（`connection_states[bot]==CONNECTED`；探针回读到 `aibot_subscribe` 帧且 `body == {bot_id, secret}`；心跳 `ping` 帧经真实 socket 到达）；`test_b120_receives_inbound_message_frame`（探针推送文本消息 → Adapter `iter_events()` 产出规范化 ChannelEnvelope：bot/message_id/external_user_id/chatid/文本全一致）；`test_b120_streams_reply_frames_back_through_socket`（`stream()` + `finish_stream()` → 探针收到 `aibot_respond_msg` 帧，`headers.req_id` 等于推送回调的 reply_id、`body.msgtype=stream`、末帧 `finish=true` 且 `stream.content == "你好世界"`）；`test_b120_proactive_send_uses_real_socket`（`send()` → 探针收到 `aibot_send_msg` 且正文一致） | 官方 `wecom-aibot-python-sdk`（`aibot.WSClient`，**未替换/未 mock**）→ 本地探针（`websockets.serve` + 真实 TLS/`wss://` + 真实 socket）→ 生产 `WeComAdapter`（经生产 `_AibotClientPort` 端口包装，仅把 `ws_url` 指向探针）；自签证书只放宽客户端校验（`aibot.ws._SSL_CONTEXT` 置 unverified，测试内 monkeypatch 并自动还原），协议与传输未改；不声称企业微信实网验收 | verified |

补充记录：
- 新增物：`tests/e2e/wecom_probe_app.py`（真实 WS 协议探针：认证/心跳/消息与事件推送/回复与主动发送 ack + 帧回读与等待工具 + 自签证书生成）、`tests/acceptance/im_gateway/test_wecom_boundary.py`（B-120 用例）。**生产代码零改动**（`git status` 仅新增上述两个测试文件）。
- 探针只承载企业微信端协议，不替代生产 Adapter/SDK；`FakeChannelAdapter`/`MockTransport` 未参与本场景（TASK-031 会在此基础上补握手拒绝/断线/发送失败注入）。
- 回归：`tests/gateway` → `161 passed`；非验收全量 → `1161 passed`。
- 外部依赖：无（SDK 已在依赖中：`wecom-aibot-python-sdk>=1,<2`）。
- 清理：探针 `server.close() + wait_closed()`；Adapter `stop()` 取消连接任务；自签证书写入临时目录，进程退出即弃。
- B-120: verified — automated command passed; run_id=5385a075a71c42879e920fcb30174bed (confirmed_by: runner)

### Log
- [2026-09-20] prepared (draft)
- [2026-09-21] 用户确认后写入；设计修订已承接，状态保持 draft。

---
- [2026-09-23] started
- [2026-09-23] completed (done)
## TASK-021: 建立 Gateway 基础真实验收环境

- **Status**: done
- **Priority**: P0
- **Depends**: TASK-020
- **Source**: 10-im-gateway.backend.design.md#2.5.2 功能验收场景, 10-im-gateway.backend.design.md#3.5 质量实现方案
- **Spec-Refs**: 
- **Acceptance-Refs**: B-121
- **Files**: `tests/acceptance/im_gateway/conftest.py`, `tests/acceptance/im_gateway/test_environment.py`, `tests/e2e/seed_im_gateway.py`
- **Estimate**: 半天级（进程编排 + 种子 + 清理；两个 Runtime 实例与 Worker 已拆到 TASK-030，WS 故障注入已拆到 TASK-031）。若仍超一天，按「基础进程与探针」/「种子与清理」再拆。

### Description

复用 Runtime/Console E2E 设施启动真实 Console、Gateway、单个 Runtime 实例、PostgreSQL、Redis 与模型 HTTP 探针，并接线 TASK-020 的 WS 探针；支持进程级断线与重启；数据用 e2e-im-* 且 fixture finally 清理。缺依赖明确失败/阻塞，不用 skip 充当证据。两个 Runtime 实例与 Worker 的扩展见 TASK-030。

### Checklist

- [x] [B-121][integration] 修改生产代码前先按 生产进程生命周期→真实HTTP/PostgreSQL/Redis 编写或扩展用例并记录 RED；关键断言：进程健康可探测；模型经真实HTTP；进程级断线/重启可恢复；无残留DB数据、键或后台进程。执行 argv：`["uv","run","pytest","-q","tests/acceptance/im_gateway/test_environment.py","-k","b121"]`。
- [x] 实现或补齐：复用 Runtime/Console E2E 设施启动真实进程（Console/Gateway/Runtime）、PostgreSQL、Redis 和模型/WS 探针；支持进程级断线与重启；数据用 e2e-im-* 且 fixture finally 清理。缺依赖明确失败/阻塞，不用 skip 充当证据。
- [x] 执行上述契约命令，填写 Acceptance Evidence 的 RED/GREEN、每个关键断言位置、真实组件与清理记录；失败、skip 或外部阻塞保留未验证。所有代码改动有对应测试，函数≤50行，强类型与显式异常处理。

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| B-121 | integration | 生产进程生命周期→真实HTTP/PostgreSQL/Redis（Console/Gateway/模型探针/种子与清理） | 进程健康可探测；模型经真实HTTP；进程级断线/重启可恢复；无残留DB数据、键或后台进程 | tests/acceptance/im_gateway/test_environment.py / B-121（verified） | `["uv","run","pytest","-q","tests/acceptance/im_gateway/test_environment.py","-k","b121"]` | verified |

### Acceptance Evidence

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|---|---|---|---|---|---|
| B-121 | **首次运行失败链（据实记录）**：`uv run pytest -q tests/acceptance/im_gateway/test_environment.py -k b121` 连续暴露 5 个真实问题并逐个修好：①`ServiceProcess` 自行拼接 `:app`，栈里传 `模块:app` → Console 退出 code=3；②`CHANNEL_PROBE_URL` 会让 Gateway 整体换用 HTTP 探针适配器（`main.py:35`），真实 WS 连接根本不建立；③bot 首次连接退避期间 `/readyz` 返回 503（见"发现"）；④`control.agent_access_grant` 无 `tenant_id` 列（改 join 统计）；⑤purge/count/seed 复用被缓存的 engine 触发 `attached to a different loop` → 全部改为各自独立 engine | 修完后同一命令 → `5 passed`（16s） | `test_b121_all_service_processes_are_reachable`（Console/Runtime/Gateway 进程存活 + `/healthz` 200 + 有界等待 `/readyz` 200）；`test_b121_gateway_authenticates_to_local_ws_probe`（探针回读到 `aibot_subscribe` 且 `body=={bot_id,secret}`、无认证失败、`/readyz` ready）；`test_b121_model_is_called_over_real_http`（模型探针 `/healthz` 200 且库中 `model_definition.base_url` 指向该真实探针）；`test_b121_process_restart_recovers`（停 Console → 进程确已退出、Gateway 仍 ready=200（设计 §4.1 已有完整快照可服务）→ 重启后 Console/Gateway 均健康）；`test_b121_cleanup_leaves_no_tenant_residue`（`purge_tenant()` 后 6 张 control 表 + grant join 计数全为 0，Redis 无本租户 `im:dedupe:*` 键） | 真实 uvicorn 子进程（Console / Runtime / Gateway，真实 socket）+ 真实 PostgreSQL + 真实 Redis + 真实模型 HTTP 探针（`tests.e2e.openai_probe_app`）+ 真实 WS 协议探针（`wss://` 真实 TLS，自签 CA 经 `WECOM_WS_CA_FILE` 注入，未 mock 任何一条边界） | verified |

**发现（不属本任务修复范围，登记归属）**：
1. `/readyz` 在 bot 首次连接退避期间返回 **503**（`failed:["adapters"]`，"adapters" 由 `adapter.healthy()` 推导），与 design §4.1「必要 Bot connection manager 已初始化；单 bot 故障在 detail 标记 degraded 并退避，不要求全部 CONNECTED」不一致 → 归 **TASK-007 / B-107**（本任务以"有界等待 ready"表达环境就绪，不改就绪语义）。
2. `CHANNEL_PROBE_URL` 是**整体替换**渠道适配器（`main.py:35`），因此投递验收（TASK-027）需用自己的栈配置；基础环境不设置该变量（已在 environment.py 注释说明）。

补充记录：
- 生产改动（最小 seam，默认关闭）：`SharedSettings` 增加 `wecom_ws_url` / `wecom_ws_ca_file`；`build_wecom_sdk_client` 传 `ws_url`，并在显式配置 CA 时把本地探针自签 CA 交给官方 SDK（SDK 把 SSL context 固定在模块级且写死 certifi，无法按连接注入）。默认空值 = 官方地址 + certifi 校验，生产路径不变。
- 复用而非重写：09 验收栈原语（`ServiceProcess` / `free_port` / `require` / `run_db` / `clear_engine_caches` / 三组清理 SQL）+ 既有模型探针与 WS 探针。
- 文件比计划多一个 `tests/acceptance/im_gateway/environment.py`（把栈实现从 conftest 拆出，供 TASK-030/031/022+ 复用）；计划中的 `conftest.py` / `test_environment.py` / `tests/e2e/seed_im_gateway.py` 均按计划落地。
- 回归：`tests/gateway + tests/console_channel` → `197 passed`；非验收全量 → `1161 passed`。
- 数据与清理：租户固定 `e2e-im-gateway`，fixture finally 幂等清理（`purge_tenant` + 进程 stop + 探针 close），并有专门用例断言无残留行/键/进程。
- B-121: verified — automated command passed; run_id=c4a4a78e73e74a8fa486cd90fd2121de (confirmed_by: runner)

### Log
- [2026-09-20] prepared (draft)
- [2026-09-21] 用户确认后写入；设计修订已承接，状态保持 draft。

---
- [2026-09-23] started
- [2026-09-23] completed (done)
## TASK-022: 验收多 Bot 路由与任意 Runtime 实例

- **Status**: draft
- **Priority**: P0
- **Depends**: TASK-005, TASK-006, TASK-009, TASK-013, TASK-016, TASK-021, TASK-030
- **Source**: 10-im-gateway.backend.design.md#2.5.2 功能验收场景, 10-im-gateway.backend.design.md#3.1 技术选型与关键决策, 10-im-gateway.backend.design.md#Spec Compliance Matrix
- **Spec-Refs**: harness-arch#RULE-arch-001, harness-im#RULE-im-001
- **Acceptance-Refs**: B-122, S-01, RULE-01, RULE-04, RISK-01, RULE-arch-001, RULE-im-001
- **Files**: `tests/acceptance/im_gateway/test_routing.py`, `tests/architecture/test_im_gateway_boundaries.py`
- **Estimate**: 半天级（7 条义务：2 个 E2E 场景 + 3 条业务规则 + 2 条 required Rule 的联合命令）；超出先拆规则项与场景项。
- **External-Depends**: EXT-08（真实 Runtime Run/SSE 契约与任意实例行为证据）

### Description

两个 bot 指向同一 Agent，以真实消息进入 Gateway，经正常服务入口分别在两个 Runtime 实例执行（双实例环境由 TASK-030 提供）；验证 SDK 类型隔离、四部署单元及无 bot/Agent→Pod 映射。

### Checklist

- [ ] [B-122][E2E] 以 owner 实现任务已完成为前提（验收类不制造 RED，见 Baseline）按 官方SDK/WeComAdapter→Gateway→Console/PG→真实双Runtime HTTP 编写或扩展用例；关键断言：两bot同逻辑Agent，实例可替换；无路由绑Pod；Runtime/Worker不导入SDK；禁用bot无Run。执行 argv：`["uv","run","pytest","-q","tests/acceptance/im_gateway/test_routing.py","-k","b122"]`。
- [ ] [S-01][E2E] 以 owner 实现任务已完成为前提（验收类不制造 RED，见 Baseline）按 官方SDK/WeComAdapter→Gateway→真实Console/PG与双Runtime 编写或扩展用例；关键断言：两个bot同一Agent；可进入不同Runtime；无Pod绑定。执行 argv：`["uv","run","pytest","-q","tests/acceptance/im_gateway/test_routing.py","-k","s01"]`。
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
- **External-Depends**: EXT-08（真实 Runtime Run 契约，普通消息分支需要）

### Description

迁移并扩展已有只覆盖 ConsoleClient→Console→PG 的 S-02（`tests/e2e/test_gateway_bind_e2e.py::test_s02_*`），纳入真实 Gateway 命令处理与最终回复：旧用例内容并入 `tests/acceptance/im_gateway/test_binding.py` 后删除原用例，避免同一场景 ID 双轨（验收命令只执行新文件）。覆盖无效/过期/已用绑定码以及统一 envelope/catalog、页码边界。

### Checklist

- [ ] [B-123][E2E] 以 owner 实现任务已完成为前提（验收类不制造 RED，见 Baseline）按 真实WS→Gateway命令→Console bind HTTP→PostgreSQL→SDK回复 编写或扩展用例；关键断言：绑定成功与身份记录一致；错误HTTP/code/msg从catalog映射；trace/request/timestamp完整；无授权扩张。执行 argv：`["uv","run","pytest","-q","tests/acceptance/im_gateway/test_binding.py","-k","b123"]`。
- [ ] [S-02][E2E] 以 owner 实现任务已完成为前提（验收类不制造 RED，见 Baseline）按 Gateway完整/bind命令→真实Console HTTP→PostgreSQL→最终回复 编写或扩展用例；关键断言：有效码绑定成功、回复已验证；身份持久；不隐式授予Agent权限。执行 argv：`["uv","run","pytest","-q","tests/acceptance/im_gateway/test_binding.py","-k","s02"]`。
- [ ] [E-02][integration] 以 owner 实现任务已完成为前提（验收类不制造 RED，见 Baseline）按 真实Console bind HTTP→PostgreSQL bind_code/identity 编写或扩展用例；关键断言：无效/已用BIND_CODE_INVALID；过期BIND_CODE_EXPIRED；事务无副作用。执行 argv：`["uv","run","pytest","-q","tests/acceptance/im_gateway/test_binding.py","-k","e02"]`。
- [ ] 实现或补齐：扩展已有只覆盖 ConsoleClient→Console→PG 的 S-02，纳入真实 Gateway 命令处理与最终回复；覆盖无效/过期/已用绑定码以及统一 envelope/catalog、页码边界。
- [ ] [RULE-api-001][E2E] verifier_ref=harness-api#RULE-api-001；原 verifier 输入 argv=`["uv","run","pytest","-q","tests/test_api_i18n.py","tests/test_error_catalog.py","tests/acceptance/test_foundation_api_envelope.py"]`；补充真实边界 真实WS→Gateway命令→Console bind HTTP→PostgreSQL→SDK回复；联合映射 B-101（TASK-001 公共契约与页码边界）/ B-105（TASK-005 快照分页与 revision）；原 Spec verifier 真实边界，断言 绑定成功与身份记录一致；错误HTTP/code/msg从catalog映射；trace/request/timestamp完整；无授权扩张；**列表统一 items/page/page_size/total 且 page>=1、1<=page_size<=100（分页不得借小集合豁免）**；原 verifier 全部通过，联合验收 argv=`["bash","-lc","uv run pytest -q tests/acceptance/im_gateway/test_binding.py && uv run pytest -q tests/gateway/test_channel_contracts.py -k b101 && uv run pytest -q tests/gateway/test_bot_snapshot.py -k b105 && uv run pytest -q tests/test_api_i18n.py tests/test_error_catalog.py tests/acceptance/test_foundation_api_envelope.py"]`。
- [ ] [RULE-02][E2E] 作为唯一最终负责人，沿 真实WS→Gateway命令→Console bind HTTP→PostgreSQL→SDK回复 验证 统一封套、catalog错误码及分页；联合映射 S-02 / E-02；命令 `["uv","run","pytest","-q","tests/acceptance/im_gateway/test_binding.py","-k","b123"]`，不得以任务标题或静态声明代替行为证据。
- [ ] 执行上述契约命令，填写 Acceptance Evidence 的 RED/GREEN、每个关键断言位置、真实组件与清理记录；失败、skip 或外部阻塞保留未验证。所有代码改动有对应测试，函数≤50行，强类型与显式异常处理。

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| B-123 | E2E | 真实WS→Gateway命令→Console bind HTTP→PostgreSQL→SDK回复 | 绑定成功与身份记录一致；错误HTTP/code/msg从catalog映射；trace/request/timestamp完整；无授权扩张 | tests/acceptance/im_gateway/test_binding.py / B-123（planned） | `["uv","run","pytest","-q","tests/acceptance/im_gateway/test_binding.py","-k","b123"]` | planned |
| S-02 | E2E | Gateway完整/bind命令→真实Console HTTP→PostgreSQL→最终回复 | 有效码绑定成功、回复已验证；身份持久；不隐式授予Agent权限 | tests/acceptance/im_gateway/test_binding.py / S-02（planned） | `["uv","run","pytest","-q","tests/acceptance/im_gateway/test_binding.py","-k","s02"]` | planned |
| E-02 | integration | 真实Console bind HTTP→PostgreSQL bind_code/identity | 无效/已用BIND_CODE_INVALID；过期BIND_CODE_EXPIRED；事务无副作用 | tests/acceptance/im_gateway/test_binding.py / E-02（planned） | `["uv","run","pytest","-q","tests/acceptance/im_gateway/test_binding.py","-k","e02"]` | planned |
| RULE-02 | E2E | 真实WS→Gateway命令→Console bind HTTP→PostgreSQL→SDK回复 | 统一封套、catalog错误码及分页；联合映射 S-02 / E-02 | tests/acceptance/im_gateway/test_binding.py / RULE-02（planned） | `["uv","run","pytest","-q","tests/acceptance/im_gateway/test_binding.py","-k","b123"]` | planned |
| RULE-api-001 | E2E | 真实WS→Gateway命令→Console bind HTTP→PostgreSQL→SDK回复；分页契约（B-101/B-105）与统一列表语义；原 Spec verifier 真实边界 | 绑定成功与身份记录一致；错误HTTP/code/msg从catalog映射；trace/request/timestamp完整；无授权扩张；列表统一 items/page/page_size/total 且分页边界正确；原 verifier 全部通过 | tests/acceptance/im_gateway/test_binding.py + tests/gateway/test_channel_contracts.py + tests/gateway/test_bot_snapshot.py + 原 verifier / RULE-api-001（planned） | `["bash","-lc","uv run pytest -q tests/acceptance/im_gateway/test_binding.py && uv run pytest -q tests/gateway/test_channel_contracts.py -k b101 && uv run pytest -q tests/gateway/test_bot_snapshot.py -k b105 && uv run pytest -q tests/test_api_i18n.py tests/test_error_catalog.py tests/acceptance/test_foundation_api_envelope.py"]` | planned |

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
- **External-Depends**: EXT-08（真实 Runtime Prompt/ToolRegistry 授权生效证据）

### Description

用真实 Grant/Binding/SELECTED 数据验证 /skills 目录和普通消息授权；覆盖 Agent/资源 enabled/is_deleted，以及 /new 不变更授权/记忆。

### Checklist

- [ ] [B-124][E2E] 以 owner 实现任务已完成为前提（验收类不制造 RED，见 Baseline）按 WS命令→Gateway→Console授权HTTP/PG→Runtime Prompt/ToolRegistry 编写或扩展用例；关键断言：未授权资源名称/描述/Prompt/Tool/SkillCatalog均不可见；没有绑定启停/授权到期/三元授权；新会话不改变绑定。执行 argv：`["uv","run","pytest","-q","tests/acceptance/im_gateway/test_authorization.py","-k","b124"]`。
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
- **Spec-Refs**: 
- **Acceptance-Refs**: B-125, S-03, E-04, RULE-10
- **Files**: `tests/acceptance/im_gateway/test_runtime_stream.py`
- **Estimate**: 半天级（2 条 E2E + 1 条 integration + 1 条业务规则联合命令）；超出先拆场景项与规则项。
- **External-Depends**: EXT-08（真实 Runtime 契约与对应验收 Evidence）

### Description

验证真实 Run 流、普通消息自动恢复、忙碌拒绝与取消错误语义；配置变更后当前快照不变、新 Run 使用新配置。断流回收（E-03）、Snapshot 冻结与终态 CAS 的 required Rule 归属（RULE-06 / RULE-snapshot-001）见 TASK-032，本任务只在其流式用例中断言快照沿用的可观测结果，不重复登记规则 owner。

### Checklist

- [ ] [B-125][E2E] 以 owner 实现任务已完成为前提（验收类不制造 RED，见 Baseline）按 真实WS→Gateway→Runtime HTTP/SSE→PostgreSQL Snapshot/Reaper→SDK回复 编写或扩展用例；关键断言：seq单调；resumed=true沿用原Run；RUN_BUSY无新Run；断流文案正确且Reaper FAILED/RUN_ABANDONED；Snapshot冻结与终态CAS。执行 argv：`["uv","run","pytest","-q","tests/acceptance/im_gateway/test_runtime_stream.py","-k","b125"]`。
- [ ] [S-03][E2E] 以 owner 实现任务已完成为前提（验收类不制造 RED，见 Baseline）按 真实WeCom协议WS→Gateway→Runtime SSE/PG→官方SDK出站 编写或扩展用例；关键断言：授权消息创建Run；seq单调；run.completed正确收尾；无业务API mock。执行 argv：`["uv","run","pytest","-q","tests/acceptance/im_gateway/test_runtime_stream.py","-k","s03"]`。
- [ ] [E-04][integration] 以 owner 实现任务已完成为前提（验收类不制造 RED，见 Baseline）按 真实Runtime/PG→Gateway HTTP错误处理 编写或扩展用例；关键断言：CREATED/RUNNING冲突返回RUN_BUSY；无新Run且原状态不变；提示可/stop。执行 argv：`["uv","run","pytest","-q","tests/acceptance/im_gateway/test_runtime_stream.py","-k","e04"]`。
- [ ] 实现或补齐：验证真实 Run 流、普通消息自动恢复、忙碌拒绝与取消错误语义；配置变更后当前快照不变、新 Run 使用新配置。断流回收与 Snapshot/CAS 的规则归属见 TASK-032。
- [ ] [RULE-10][E2E] 作为唯一最终负责人，沿 真实WS→Gateway→Runtime HTTP/SSE→PostgreSQL Snapshot/Reaper→SDK回复 验证 未绑定正常分支、自动resume、并发和取消错误语义；联合映射 S-06 / E-04 / E-05 / B-125；命令 `["uv","run","pytest","-q","tests/acceptance/im_gateway/test_runtime_stream.py","-k","b125"]`，不得以任务标题或静态声明代替行为证据。断言须同时覆盖 B-125 的关键项（seq 单调、resumed=true、RUN_BUSY 无新 Run、取消/终态文案），不得只跑通 b125 用例名称即视为满足。
- [ ] 执行上述契约命令，填写 Acceptance Evidence 的 RED/GREEN、每个关键断言位置、真实组件与清理记录；失败、skip 或外部阻塞保留未验证。所有代码改动有对应测试，函数≤50行，强类型与显式异常处理。

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| B-125 | E2E | 真实WS→Gateway→Runtime HTTP/SSE→PostgreSQL Snapshot/Reaper→SDK回复 | seq单调；resumed=true沿用原Run；RUN_BUSY无新Run；断流文案正确且Reaper FAILED/RUN_ABANDONED；Snapshot冻结与终态CAS | tests/acceptance/im_gateway/test_runtime_stream.py / B-125（planned） | `["uv","run","pytest","-q","tests/acceptance/im_gateway/test_runtime_stream.py","-k","b125"]` | planned |
| S-03 | E2E | 真实WeCom协议WS→Gateway→Runtime SSE/PG→官方SDK出站 | 授权消息创建Run；seq单调；run.completed正确收尾；无业务API mock | tests/acceptance/im_gateway/test_runtime_stream.py / S-03（planned） | `["uv","run","pytest","-q","tests/acceptance/im_gateway/test_runtime_stream.py","-k","s03"]` | planned |
| E-04 | integration | 真实Runtime/PG→Gateway HTTP错误处理 | CREATED/RUNNING冲突返回RUN_BUSY；无新Run且原状态不变；提示可/stop | tests/acceptance/im_gateway/test_runtime_stream.py / E-04（planned） | `["uv","run","pytest","-q","tests/acceptance/im_gateway/test_runtime_stream.py","-k","e04"]` | planned |
| RULE-10 | E2E | 真实WS→Gateway→Runtime HTTP/SSE→PostgreSQL Snapshot/Reaper→SDK回复 | 未绑定正常分支、自动resume、并发和取消错误语义；联合映射 S-06 / E-04 / E-05 / B-125 | tests/acceptance/im_gateway/test_runtime_stream.py / RULE-10（planned） | `["uv","run","pytest","-q","tests/acceptance/im_gateway/test_runtime_stream.py","-k","b125"]` | planned |

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

补新增必选 Rule 的真实验收；同消息同payload、并发与进程重启后回读持久首次结果；同key不同指纹不执行副作用。明确期望 409 `IDEMPOTENCY_MISMATCH`（required RULE-api-002；指纹按规范化 JSON SHA256）；既有 Runtime/Worker/Console 幂等原语已一致，不存在待对齐差异。另覆盖 /new 同命令重放不创建第二会话；不接受其他错误码替代。

### Checklist

- [ ] [B-126][E2E] 以 owner 实现任务已完成为前提（验收类不制造 RED，见 Baseline）按 Gateway HTTP→Console/Runtime→真实PostgreSQL幂等表/partial unique→可观测副作用 编写或扩展用例；关键断言：稳定Idempotency-Key；一次绑定/Run/新会话；首次结果200重放；租户和endpoint隔离；异指纹409 `IDEMPOTENCY_MISMATCH`。执行 argv：`["uv","run","pytest","-q","tests/acceptance/im_gateway/test_idempotency.py","-k","b126"]`。
- [ ] 实现或补齐：补新增必选 Rule 的真实验收；同消息同payload、并发与进程重启后回读持久首次结果；同key不同指纹不执行副作用。明确期望 409 `IDEMPOTENCY_MISMATCH`（required RULE-api-002；指纹按规范化 JSON SHA256）；既有 Runtime/Worker/Console 幂等原语已一致，不存在待对齐差异。另覆盖 /new 同命令重放不创建第二会话；不接受其他错误码替代。
- [ ] [RULE-api-002][E2E] verifier_ref=harness-api#RULE-api-002；原 verifier 输入 argv=`["uv","run","pytest","-q","tests/console_skill/test_import_idempotency.py"]`；补充真实边界 Gateway HTTP→Console/Runtime→真实PostgreSQL幂等表/partial unique→可观测副作用；联合映射 B-103（TASK-003 绑定持久幂等）/ B-111（TASK-011 /new 幂等）；原 Spec verifier 真实边界，断言 稳定Idempotency-Key；一次绑定/Run/新会话；首次结果200重放；租户和endpoint隔离；异指纹409 `IDEMPOTENCY_MISMATCH`；原 verifier 全部通过，联合验收 argv=`["bash","-lc","uv run pytest -q tests/acceptance/im_gateway/test_idempotency.py && uv run pytest -q tests/console_channel/test_channel_bind_idempotency.py -k b103 && uv run pytest -q tests/gateway/test_commands_integration.py -k b111 && uv run pytest -q tests/console_skill/test_import_idempotency.py"]`。
- [ ] 执行上述契约命令，填写 Acceptance Evidence 的 RED/GREEN、每个关键断言位置、真实组件与清理记录；失败、skip 或外部阻塞保留未验证。所有代码改动有对应测试，函数≤50行，强类型与显式异常处理。

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| B-126 | E2E | Gateway HTTP→Console/Runtime→真实PostgreSQL幂等表/partial unique→可观测副作用 | 稳定Idempotency-Key；一次绑定/Run/新会话；首次结果200重放；租户和endpoint隔离；异指纹409 `IDEMPOTENCY_MISMATCH` | tests/acceptance/im_gateway/test_idempotency.py / B-126（planned） | `["uv","run","pytest","-q","tests/acceptance/im_gateway/test_idempotency.py","-k","b126"]` | planned |
| RULE-api-002 | E2E | Gateway HTTP→Console/Runtime→真实PostgreSQL幂等表/partial unique→可观测副作用（含 B-103 / B-111）；原 Spec verifier 真实边界 | 稳定Idempotency-Key；一次绑定/Run/新会话；首次结果200重放；租户和endpoint隔离；异指纹409 `IDEMPOTENCY_MISMATCH`；原 verifier 全部通过 | tests/acceptance/im_gateway/test_idempotency.py + tests/console_channel/test_channel_bind_idempotency.py + tests/gateway/test_commands_integration.py + 原 verifier / RULE-api-002（planned） | `["bash","-lc","uv run pytest -q tests/acceptance/im_gateway/test_idempotency.py && uv run pytest -q tests/console_channel/test_channel_bind_idempotency.py -k b103 && uv run pytest -q tests/gateway/test_commands_integration.py -k b111 && uv run pytest -q tests/console_skill/test_import_idempotency.py"]` | planned |

### Acceptance Evidence
> planned。编码期填 RED/GREEN 命令与结果、断言路径/用例/位置、真实组件证据、外部依赖状态与清理证据；全部 verified 才可 done。本次结构检查不代表功能测试通过。

### Log
- [2026-09-20] prepared (draft)
- [2026-09-21] 用户确认后写入；设计修订已承接，状态保持 draft。

---

## TASK-027: 验收 Worker 主动投递及 Redis 故障

- **Status**: draft
- **Priority**: P0
- **Depends**: TASK-008, TASK-017, TASK-021, TASK-030
- **Source**: 10-im-gateway.backend.design.md#2.5.2 功能验收场景, 10-im-gateway.backend.design.md#API-05 主动投递, 10-im-gateway.backend.design.md#3.2.3 入站去重, 10-im-gateway.backend.design.md#5. 风险与依赖
- **Spec-Refs**: 
- **Acceptance-Refs**: B-127, S-04, E-06, RULE-09, RISK-02
- **Files**: `tests/acceptance/im_gateway/test_delivery.py`, `tests/acceptance/im_gateway/test_redis_degradation.py`
- **Estimate**: 半天级（E2E 投递链路 + Redis 降级 + Worker 进程编排）；超出先拆 E2E 与降级两项。
- **External-Depends**: EXT-09-020 / EXT-09-021 / EXT-09-043（**已满足**，2026-09-23 复核：09 归档 TASK-020/021/043 verified，实现已落地并被 09 的 S-03/B-121/B-143/B-209 覆盖）。本任务只补本模块的跨模块 E2E 与降级语义，不重复实现投递去重。

### Description

复用 EXT-09-020/021/043 已落地的可靠投递实现与证据，以本模块 S-04/E-06 验证 Worker（TASK-030 提供）→Gateway→SDK 完整链路；入站和投递 Redis 故障均继续 at-least-once，发送失败不误标成功。既有 09 证据（`tests/acceptance/task_schedule/test_delivery.py`、`tests/gateway/test_delivery_api.py`）作为前置事实引用，不重复登记为本任务场景。

### Checklist

- [ ] [B-127][E2E] 以 owner 实现任务已完成为前提（验收类不制造 RED，见 Baseline）按 真实Worker/PG→Gateway HTTP→真实Redis→官方SDK/WS接收端 编写或扩展用例；关键断言：路由准确；首次发送/重复200不重发；TTL7d；失败可重试，Worker最多5次后FAILED；故障/不确定发送允许重复但不吞业务事实。执行 argv：`["uv","run","pytest","-q","tests/acceptance/im_gateway/test_delivery.py","-k","b127"]`。
- [ ] [S-04][E2E] 以 owner 实现任务已完成为前提（验收类不制造 RED，见 Baseline）按 真实Worker→Gateway HTTP→Redis→官方SDK/真实WS接收 编写或扩展用例；关键断言：delivery_key固定；按route推送最终结果；重放200/deduplicated=true且不重发。执行 argv：`["uv","run","pytest","-q","tests/acceptance/im_gateway/test_delivery.py","-k","s04"]`。
- [ ] [E-06][integration] 以 owner 实现任务已完成为前提（验收类不制造 RED，见 Baseline）按 Gateway入站/投递→真实Redis连接故障→Runtime/WS 编写或扩展用例；关键断言：两条路径均at-least-once继续；故障时允许重复但不吞业务事实；恢复后去重恢复。执行 argv：`["uv","run","pytest","-q","tests/acceptance/im_gateway/test_redis_degradation.py","-k","e06"]`。
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
- **Depends**: TASK-006, TASK-007, TASK-017, TASK-021
- **Source**: 10-im-gateway.backend.design.md#2.5.2 功能验收场景, 10-im-gateway.backend.design.md#3.2.2 Bot 快照轮询与 Secret 解析, 10-im-gateway.backend.design.md#3.5 质量实现方案, 10-im-gateway.backend.design.md#Spec Compliance Matrix
- **Spec-Refs**: harness-secret#RULE-secret-001
- **Acceptance-Refs**: B-128, E-07, RULE-03, RISK-03, RULE-secret-001
- **Files**: `tests/acceptance/im_gateway/test_secrets_and_readiness.py`
- **Estimate**: 15–60 分钟；超出先拆分

### Description

在真实 DB 与受保护内部快照链路使用随机 canary secret，故障注入验证仅目标 bot 退避，其他 bot 正常；检查日志、审计、Snapshot、Prompt、外部API与IM响应。内部 bot 快照是设计指定的最小凭据传输边界，不扩散到公开端点。日志脱敏复用既有 api-kit 脱敏设施（`tests/test_logging_redaction.py`、`tests/acceptance/test_foundation_ops_audit.py` 既有覆盖），**不依赖 TASK-018**：TASK-018 只补指标与连接状态日志，其 P1 排期不影响本 P0 验收。

### Checklist

- [ ] [B-128][integration] 以 owner 实现任务已完成为前提（验收类不制造 RED，见 Baseline）按 真实PG bot secret→Console内部快照HTTP→Gateway/SDK→日志/审计/快照输出 编写或扩展用例；关键断言：缺失或错误secret不停止全部bot；readyz依据manager/完整快照而非全连接；所有禁止输出均无canary；SecretProvider无新增。执行 argv：`["uv","run","pytest","-q","tests/acceptance/im_gateway/test_secrets_and_readiness.py","-k","b128"]`。
- [ ] [E-07][integration] 以 owner 实现任务已完成为前提（验收类不制造 RED，见 Baseline）按 真实PG bot secret→Console快照→多bot SDK连接/readyz 编写或扩展用例；关键断言：坏secret只影响目标bot并退避；其他bot正常；就绪详情降级且日志无secret。执行 argv：`["uv","run","pytest","-q","tests/acceptance/im_gateway/test_secrets_and_readiness.py","-k","e07"]`。
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
- **Depends**: TASK-001, TASK-002, TASK-003, TASK-004, TASK-005, TASK-006, TASK-007, TASK-008, TASK-009, TASK-010, TASK-011, TASK-012, TASK-013, TASK-014, TASK-015, TASK-016, TASK-017, TASK-018, TASK-019, TASK-020, TASK-021, TASK-022, TASK-023, TASK-024, TASK-025, TASK-026, TASK-027, TASK-028, TASK-030, TASK-031, TASK-032
- **Source**: 10-im-gateway.backend.design.md#2.5.2 功能验收场景, 10-im-gateway.backend.design.md#5. 风险与依赖, 10-im-gateway.backend.design.md#6. 需求追溯矩阵, 10-im-gateway.backend.design.md#Spec Compliance Matrix
- **Spec-Refs**: harness-test#RULE-test-001
- **Acceptance-Refs**: B-129, RULE-07, RULE-test-001
- **Files**: `tests/acceptance/im_gateway/test_acceptance_inventory.py`
- **Estimate**: 半天级（收口全部 45 个场景/规则行 + 9 条 required Rule 证据 + 仓库级 verifier 与其等待）；本任务是唯一收口责任人，不按 15–60 分钟拆分，超一天按「场景/规则映射核对」与「仓库级 verifier 执行」两段推进。

### Description

检查设计场景、业务规则、风险及 required Rule 唯一负责人与命令；执行原 Spec verifier 及本模块真实验收，登记每个断言位置、真实组件和数据清理；环境缺失或外部依赖未完成时保持未验收，不改变层级。三点执行约束：①组合命令为仓库级（全仓 `tests/acceptance` + 前端构建 + playwright 全量），无关套件失败须单列并回到其 owner，不得据以判定本模块通过或失败；②同命令承载多条义务的组（见 Rule and Risk Traceability 的说明）执行一次须同时核对全部断言；③场景 ID 仅在本 Context 内唯一，跨需求引用必须带 Context 前缀。

### Checklist

- [ ] [B-129][integration] 以 owner 实现任务已完成为前提（验收类不制造 RED，见 Baseline）按 pytest用例收集/运行→验收Contract/Evidence→真实组件记录 编写或扩展用例；关键断言：无遗漏/重复最终负责人；新修复均有RED/GREEN；E2E无FakeAdapter/业务API mock；原verifier完整执行；失败/skip不冒充verified。执行 argv：`["uv","run","pytest","-q","tests/acceptance/im_gateway/test_acceptance_inventory.py","-k","b129"]`。
- [ ] 实现或补齐：检查设计场景、业务规则、风险及 required Rule 唯一负责人与命令；执行原 Spec verifier 及本模块真实验收，登记每个断言位置、真实组件和数据清理；环境缺失或外部依赖未完成时保持未验收，不改变层级。
- [ ] [RULE-test-001][E2E] verifier_ref=harness-test#RULE-test-001；原 verifier 输入 argv=`["bash","-lc","uv run pytest -q tests/acceptance && npm --prefix apps/console-platform/frontend run build && npm --prefix e2e test"]`；补充真实边界 真实生产HTTP/SSE、PostgreSQL、Redis、官方SDK/WS与原verifier Chrome→验收Evidence，断言 无遗漏/重复最终负责人；新修复均有RED/GREEN；E2E无FakeAdapter/业务API mock；原verifier完整执行；失败/skip不冒充verified；原 verifier 全部通过，联合验收 argv=`["bash","-lc","uv run pytest -q tests/acceptance && npm --prefix apps/console-platform/frontend run build && npm --prefix e2e test"]`。
- [ ] [RULE-07][E2E] 作为唯一最终负责人，沿 生产HTTP/SSE、PostgreSQL、Redis、官方SDK/WS与原verifier Chrome 验证 跨服务真实E2E证据；E-03保留原层级另有B-125 E2E增强；联合映射 S-01 / S-03 / E-03；命令 `["bash","-lc","uv run pytest -q tests/acceptance && npm --prefix apps/console-platform/frontend run build && npm --prefix e2e test"]`，不得以任务标题或静态声明代替行为证据。
- [ ] 执行上述契约命令，填写 Acceptance Evidence 的 RED/GREEN、每个关键断言位置、真实组件与清理记录；失败、skip 或外部阻塞保留未验证。所有代码改动有对应测试，函数≤50行，强类型与显式异常处理。

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| B-129 | integration | pytest用例收集/运行→验收Contract/Evidence→真实组件记录 | 无遗漏/重复最终负责人；新修复均有RED/GREEN；E2E无FakeAdapter/业务API mock；原verifier完整执行；失败/skip不冒充verified | tests/acceptance/im_gateway/test_acceptance_inventory.py / B-129（planned） | `["uv","run","pytest","-q","tests/acceptance/im_gateway/test_acceptance_inventory.py","-k","b129"]` | planned |
| RULE-07 | E2E | 生产HTTP/SSE、PostgreSQL、Redis、官方SDK/WS与原verifier Chrome | 跨服务真实E2E证据；E-03保留原层级另有B-125 E2E增强；B-130/B-131 为环境与探针补充证据；联合映射 S-01 / S-03 / E-03 / B-130 / B-131 | tests/acceptance/im_gateway/test_acceptance_inventory.py / RULE-07（planned） | `["bash","-lc","uv run pytest -q tests/acceptance && npm --prefix apps/console-platform/frontend run build && npm --prefix e2e test"]` | planned |
| RULE-test-001 | E2E | 真实生产HTTP/SSE、PostgreSQL、Redis、官方SDK/WS与原verifier Chrome→验收Evidence | 无遗漏/重复最终负责人；新修复均有RED/GREEN；E2E无FakeAdapter/业务API mock；原verifier完整执行；失败/skip不冒充verified；原 verifier 全部通过 | tests/acceptance/im_gateway/test_acceptance_inventory.py + 原 verifier / RULE-test-001（planned） | `["bash","-lc","uv run pytest -q tests/acceptance && npm --prefix apps/console-platform/frontend run build && npm --prefix e2e test"]` | planned |

### Acceptance Evidence
> planned。编码期填 RED/GREEN 命令与结果、断言路径/用例/位置、真实组件证据、外部依赖状态与清理证据；全部 verified 才可 done。本次结构检查不代表功能测试通过。

### Log
- [2026-09-20] prepared (draft)
- [2026-09-21] 用户确认后写入；设计修订已承接，状态保持 draft。

---

## TASK-030: 补多实例与 Worker 环境扩展

- **Status**: draft
- **Priority**: P0
- **Depends**: TASK-021
- **Source**: 10-im-gateway.backend.design.md#2.5.2 功能验收场景, 10-im-gateway.backend.design.md#3.5 质量实现方案
- **Spec-Refs**: 
- **Acceptance-Refs**: B-130
- **Files**: `tests/acceptance/im_gateway/conftest.py`, `tests/acceptance/im_gateway/test_multi_instance.py`, `tests/e2e/seed_im_gateway.py`
- **Estimate**: 半天级（第二 Runtime 实例 + Worker 进程编排、种子扩展与清理回归）

### Description

在 TASK-021 的基础环境上扩展：启动第二个 Runtime 实例与 Worker 进程，准备可被任意实例承接的种子数据，支持实例替换与 Worker 侧投递前提；缺依赖明确失败/阻塞，不用 skip 充当证据。本任务只做环境扩展，不重复 TASK-021 的进程与清理逻辑，也不实现业务行为。

### Checklist

- [ ] [B-130][integration] 修改生产代码前先按 生产第二 Runtime 实例与 Worker 进程→真实 PostgreSQL/Redis 编写或扩展用例并记录 RED；关键断言：两个 Runtime 实例均健康且可承载同一逻辑 Agent 的会话；实例替换不改变路由结论；Worker 进程可消费并产生持久投递事实；无残留 DB 数据、Redis 键或后台进程。执行 argv：`["uv","run","pytest","-q","tests/acceptance/im_gateway/test_multi_instance.py","-k","b130"]`。
- [ ] 实现或补齐：复用 TASK-021 设施扩展第二 Runtime 实例与 Worker 进程、种子与清理；环境能力以 fixture 暴露给 TASK-022/027 使用，不复制既有 fixture 逻辑。
- [ ] 执行上述契约命令，填写 Acceptance Evidence 的 RED/GREEN、每个关键断言位置、真实组件与清理记录；失败、skip 或外部阻塞保留未验证。所有代码改动有对应测试，函数≤50行，强类型与显式异常处理。

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| B-130 | integration | 生产第二 Runtime 实例与 Worker 进程→真实 PostgreSQL/Redis | 两个 Runtime 实例均健康且可承载同一逻辑 Agent 的会话；实例替换不改变路由结论；Worker 进程可消费并产生持久投递事实；无残留 DB 数据/键/进程 | tests/acceptance/im_gateway/test_multi_instance.py / B-130（planned） | `["uv","run","pytest","-q","tests/acceptance/im_gateway/test_multi_instance.py","-k","b130"]` | planned |

### Acceptance Evidence
> planned。编码期填 RED/GREEN 命令与结果、断言路径/用例/位置、真实组件证据、外部依赖状态与清理证据；全部 verified 才可 done。本次结构检查不代表功能测试通过。

### Log
- [2026-09-23] prepared (draft)；由 2026-09-23 Plan 复核从 TASK-021 拆出。

---

## TASK-031: 补 WS/SDK 故障注入边界

- **Status**: draft
- **Priority**: P0
- **Depends**: TASK-020
- **Source**: 10-im-gateway.backend.design.md#2.5.2 功能验收场景, 10-im-gateway.backend.design.md#3.1 技术选型与关键决策
- **Spec-Refs**: 
- **Acceptance-Refs**: B-131
- **Files**: `tests/e2e/wecom_probe_app.py`, `tests/acceptance/im_gateway/test_wecom_fault_injection.py`
- **Estimate**: 半天级（握手拒绝/断线/发送失败三类注入，均经真实 socket）

### Description

在 TASK-020 的探针核心上补故障注入能力：握手拒绝、连接中断、发送失败，供 TASK-006 的退避/隔离验收及收口使用。注入点必须经真实 WebSocket socket 生效，禁止以 FakeChannelAdapter/MockTransport 代替，也不替代生产 Adapter/SDK。

### Checklist

- [ ] [B-131][integration] 修改生产代码前先按 生产 WeComAdapter→真实本地 WS 服务→故障注入（握手拒绝/断线/发送失败） 编写或扩展用例并记录 RED；关键断言：三类注入均可编排并被生产 Adapter 真实观测；注入只影响目标 bot，其他 bot 连接不受影响；注入后可恢复正常；探针不宣称企业微信实网验收。执行 argv：`["uv","run","pytest","-q","tests/acceptance/im_gateway/test_wecom_fault_injection.py","-k","b131"]`。
- [ ] 实现或补齐：为探针增加可编排的握手拒绝/断线/发送失败注入接口，并保证与 TASK-020 的核心收发用例互不干扰。
- [ ] 执行上述契约命令，填写 Acceptance Evidence 的 RED/GREEN、每个关键断言位置、真实组件与清理记录；失败、skip 或外部阻塞保留未验证。所有代码改动有对应测试，函数≤50行，强类型与显式异常处理。

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| B-131 | integration | 生产 WeComAdapter→真实本地 WS 服务→故障注入（握手拒绝/断线/发送失败） | 三类注入均可编排并被生产 Adapter 真实观测；注入只影响目标 bot；注入后可恢复正常；探针不宣称企业微信实网验收 | tests/acceptance/im_gateway/test_wecom_fault_injection.py / B-131（planned） | `["uv","run","pytest","-q","tests/acceptance/im_gateway/test_wecom_fault_injection.py","-k","b131"]` | planned |

### Acceptance Evidence
> planned。编码期填 RED/GREEN 命令与结果、断言路径/用例/位置、真实组件证据、外部依赖状态与清理证据；全部 verified 才可 done。本次结构检查不代表功能测试通过。

### Log
- [2026-09-23] prepared (draft)；由 2026-09-23 Plan 复核从 TASK-020 拆出。

---

## TASK-032: 验收断流回收、Snapshot 冻结与终态 CAS

- **Status**: draft
- **Priority**: P0
- **Depends**: TASK-015, TASK-016, TASK-021
- **Source**: 10-im-gateway.backend.design.md#2.5.2 功能验收场景, 10-im-gateway.backend.design.md#3.4.1 Runtime SSE 事件处理（FEAT-03）, 10-im-gateway.backend.design.md#Spec Compliance Matrix
- **Spec-Refs**: harness-snapshot#RULE-snapshot-001
- **Acceptance-Refs**: E-03, RULE-06, RULE-snapshot-001
- **Files**: `tests/acceptance/im_gateway/test_recovery.py`
- **Estimate**: 半天级（断流回收 integration + 1 条 required Rule 联合命令含原 verifier）
- **External-Depends**: EXT-08（真实 Runtime 契约与 Reaper/Snapshot 行为证据）

### Description

验证 SSE 未收到终态即断开时的重发提示与 Runtime Reaper 回收（FAILED/RUN_ABANDONED）、终态 CAS 无重复副作用、配置变更只影响后续新 Run（快照冻结）。本任务承接 2026-09-23 复核从 TASK-025 拆出的 E-03 / RULE-06 / RULE-snapshot-001 归属，原 Spec verifier 与层级不降级；B-125 的流式部分仍由 TASK-025 负责。

### Checklist

- [ ] [E-03][integration] 以 owner 实现任务已完成为前提（验收类不制造 RED，见 Baseline）按 Gateway→真实Runtime SSE断线→Reaper/PostgreSQL 编写或扩展用例；关键断言：未收终态即断开显示重发提示；Reaper回收FAILED/RUN_ABANDONED；终态CAS。执行 argv：`["uv","run","pytest","-q","tests/acceptance/im_gateway/test_recovery.py","-k","e03"]`。
- [ ] 实现或补齐：以真实 Runtime 断流与 Reaper 行为验证回收语义，登记快照冻结的可观测结果与终态 CAS 的无重复副作用；不修改生产代码，缺陷回退 TASK-015/016。
- [ ] [RULE-snapshot-001][E2E] verifier_ref=harness-snapshot#RULE-snapshot-001；原 verifier 输入 argv=`["bash","-lc","uv run pytest -q tests/agent_runtime -k \"executor or resolve\""]`；补充真实边界 真实WS→Gateway→Runtime SSE断流/Recovery→PostgreSQL Snapshot/Reaper→终态CAS；原 Spec verifier 真实边界，断言 断流回收 FAILED/RUN_ABANDONED；Snapshot冻结只影响后续新 Run；终态CAS无重复副作用；原 verifier 全部通过，联合验收 argv=`["bash","-lc","uv run pytest -q tests/acceptance/im_gateway/test_recovery.py && uv run pytest -q tests/agent_runtime -k \"executor or resolve\""]`。
- [ ] [RULE-06][integration] 作为唯一最终负责人，沿 真实WS→Gateway→Runtime SSE断流→PostgreSQL Snapshot/Reaper→终态CAS 验证 Snapshot冻结与终态CAS；联合映射 S-03 / E-03；命令 `["uv","run","pytest","-q","tests/acceptance/im_gateway/test_recovery.py","-k","e03"]`，不得以任务标题或静态声明代替行为证据。
- [ ] 执行上述契约命令，填写 Acceptance Evidence 的 RED/GREEN、每个关键断言位置、真实组件与清理记录；失败、skip 或外部阻塞保留未验证。所有代码改动有对应测试，函数≤50行，强类型与显式异常处理。

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| E-03 | integration | Gateway→真实Runtime SSE断线→Reaper/PostgreSQL | 未收终态即断开显示重发提示；Reaper回收FAILED/RUN_ABANDONED；终态CAS | tests/acceptance/im_gateway/test_recovery.py / E-03（planned） | `["uv","run","pytest","-q","tests/acceptance/im_gateway/test_recovery.py","-k","e03"]` | planned |
| RULE-06 | integration | 真实WS→Gateway→Runtime SSE断流→PostgreSQL Snapshot/Reaper→终态CAS | Snapshot冻结与终态CAS；联合映射 S-03 / E-03 | tests/acceptance/im_gateway/test_recovery.py / RULE-06（planned） | `["uv","run","pytest","-q","tests/acceptance/im_gateway/test_recovery.py","-k","e03"]` | planned |
| RULE-snapshot-001 | E2E | 真实WS→Gateway→Runtime SSE断流/Recovery→PostgreSQL Snapshot/Reaper→终态CAS；原 Spec verifier 真实边界 | 断流回收 FAILED/RUN_ABANDONED；Snapshot冻结只影响后续新 Run；终态CAS无重复副作用；原 verifier 全部通过 | tests/acceptance/im_gateway/test_recovery.py + 原 verifier / RULE-snapshot-001（planned） | `["bash","-lc","uv run pytest -q tests/acceptance/im_gateway/test_recovery.py && uv run pytest -q tests/agent_runtime -k \"executor or resolve\""]` | planned |

### Acceptance Evidence
> planned。编码期填 RED/GREEN 命令与结果、断言路径/用例/位置、真实组件证据、外部依赖状态与清理证据；全部 verified 才可 done。本次结构检查不代表功能测试通过。

### Log
- [2026-09-23] prepared (draft)；由 2026-09-23 Plan 复核从 TASK-025 拆出。

---

## Plan Validation

- 正式任务文件：`.code-flow/tasks/2026-09-17/10-im-gateway/10-im-gateway.md`。
- Design 与 Plan 使用同一 persisted Context；9 条 required Rule 在 Design Matrix 与唯一 TASK item 绑定（本文件侧为 TASK-004/022/023/024/026/028/029/032），不改变 enforcement 或原 verifier。
- 规划校验：Context validate、design gate、plan gate、Acceptance Coverage/Contract 一致性、依赖 DAG、真实章节引用与 argv 格式；证据只表示文档结构已检查，不表示功能 GREEN。
- 2026-09-23 复核修订：任务 29→32（拆出 TASK-030 环境扩展、TASK-031 故障注入、TASK-032 断流回收与 Snapshot/CAS），补充场景 29→31（B-130/B-131），EXT-09-020/021/043 置为 satisfied，指标导出机制按 design v1.3 §4.2 落地为进程内注册表 + 真实 `/metrics`。
- 验收 manifest 由 `cf_acceptance_manifest.py` 生成并锁定 44 个 S/E/B 场景，状态全部 planned；Rule/Risk 唯一责任继续由本文件与 Spec gate 校验。
- 可复核命令：
  - `python3 .code-flow/scripts/cf_spec_context.py validate --task-dir .code-flow/tasks/2026-09-17/10-im-gateway --json`
  - `python3 .code-flow/scripts/cf_spec_gate.py --task-dir .code-flow/tasks/2026-09-17/10-im-gateway --stage design --json`
  - `python3 .code-flow/scripts/cf_spec_gate.py --task-dir .code-flow/tasks/2026-09-17/10-im-gateway --stage plan --artifact .code-flow/tasks/2026-09-17/10-im-gateway/10-im-gateway.md --json`
  - `python3 .code-flow/scripts/cf_acceptance_manifest.py --task-file .code-flow/tasks/2026-09-17/10-im-gateway/10-im-gateway.md --verify-plan --task-dir .code-flow/tasks/2026-09-17/10-im-gateway`
- 门禁通过后从 TASK-001（公共契约）或 TASK-020（WS 探针核心）开始；TASK-002/003/004/005/008/009/010/013 只需本地服务与真实 PG/Redis，可与环境任务并行推进；TASK-014 起进入 SSE/流式链路（TASK-015 依赖 TASK-020 探针）。TASK-021（基础环境）、TASK-030（多实例/Worker）、TASK-031（故障注入）就绪后再执行依赖它们的验收任务（022–029、032）。External-Depends 必须在对应 TASK 启动前满足（当前错误码差异已消除，EXT-08 仅剩真实链路证据核对）。
