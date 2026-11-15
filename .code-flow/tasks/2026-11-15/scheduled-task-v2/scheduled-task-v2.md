---
{"version":1,"requirement":"scheduled-task-v2"}
---
# Tasks: 定时任务与自然语言调度 v2

- **Created**: 2026-11-15
- **Workflow**: full
- **Inputs**: [后端设计](scheduled-task-v2.backend.design.md)、[前端设计](scheduled-task-v2.frontend.design.md)、[人工验收场景](scheduled-task-v2.scenarios.md)
- **Plan Status**: 待任务边界、Spec-Refs、Scenarios、Tests 复核；所有 TASK 为 draft，未开始实现或业务验收。

## 目标与范围

交付自然语言创建/管理的 REMINDER、SKILL、完整 AGENTIC 调度，支持 Draft 用户确认、不可变 Revision、Fire 历史、Snapshot v4、三种并发策略、Task-owned ASYNC/容量/产物、Human 恢复、WeCom PUSH、WebChat pull 任务中心与 Console 运营。BATCH 继续是单次执行内部类型，立即任务既有能力同步适配新 schema。

MSSWWEB 只交付 TaskCenterService/contracts 与 Fake surface 契约，任务中心路由、BFF 页面及接入文档另立需求。RRULE/工作日历、补发策略、Skill input schema、独立待办/通知中心、Console 创建/意图编辑、多人审批与 Multi-Agent Team 不在本需求。

## 执行与验证约定

- 一个需求一份任务文档。按本文件路径和明确 TASK ID 绑定，不从状态猜当前任务。依赖全部 done 才开始；中间 TASK finish 通过后继续后续 TASK，不逐项等待人工 review。
- 每个 TASK 同步增加有意义的行为/契约回归。Tests 中 `test_scheduled_v2_*` 为本需求计划新增文件，实施该 TASK 时创建；其它既有测试同步更新断言，不能仅修改测试以掩盖回归。
- Tests 的 cwd 均为仓库根，argv 不使用 shell 通配符。新增用例按 unit/contract/integration 标记，沿用既有测试目录和隔离数据仓库机制；关键断言因缺失依赖而 skip 不算完成。
- `code-flow task finish <本文件> --task TASK-NNN` 只执行该 TASK 声明的相关测试；Runtime 适配切换例外扩大到完整 `tests/agent_runtime` 相关回归，这是 Worker Agentic 的前置门槛。这里不声明全仓 pytest 或完整 acceptance/E2E 命令。
- 数据库测试使用现有临时/克隆空库和独立 Redis；运行前停 dev 服务、查 reload Worker/孤儿进程。不共享 dev 库。单跑绿整跑偶发红先查残留、复跑一次再判断。
- `E2E: ci:e2e` 只表示交付真实浏览器旅程及 CI 接线，不是已通过。浏览器与跨服务 acceptance 的执行及结果独立记录，成功路径不得伪造业务响应。人工场景保留在原 scenarios 文件，本表分配主责任，不把它改成 E2E 注册表。
- 所有 TASK 都使用 `Skip-Tests: false`；失败时不写完成。提交信息必须等 finish 实际返回后再写，不能把 finish 与 git commit 串成预写完成结论的链。
- Spec-Refs 均相对 `.code-flow/specs` 且不带扩展名；实施前读所引当前 Spec 和本 TASK 的 Spec Delta。Delta 是评审中的变更提案，当前不直接改已接受 Rules。
- 最后一个 TASK finish 后停下，等待用户完整功能验收与代码审查；明确确认前不 archive。archive 只应用已复核 Delta 并移动需求，CI/人工独立负责全量验证。

## 实施门槛与关键顺序

| 门槛 | 必须交付的事实 | 后续可开展的能力 |
|---|---|---|
| 契约与 schema | 严格类型、循环 FK、owner XOR、事件 FK、隔离夹具可验证；workspace 保留 | 原子业务写入及 owner API |
| 持久执行基础 | 幂等/锁序/终态、recurrence/配额、Admission/Snapshot、Draft/确认/生命周期、Fire、claim、现有 Skill/立即链适配 | Runtime 完整相关回归 |
| Runtime 切换 | 中立 Kernel 端口已用于 Runtime 生产路径，相关全目录回归通过 | Worker Agentic 适配器接入；不允许提前接入后补 Runtime |
| Worker 完整能力 | Agentic parity、ASYNC 多结果恢复、Human 双侧 CAS、独立 Delivery | TaskCenter/Console 与真实旅程 |
| 用户面与运维 | WebChat/WeCom/Console、保留清理、指标/loop/发布操作 | CI 完整验收与用户人工复核 |

依赖表是开发与验证顺序；破坏性迁移后的孤立中间版本不能直接上线。现有 Skill/立即任务先同步协议以支撑 Runtime 回归，Worker Agentic 必须等待 Runtime 门槛。统一排空旧在途后切换相关消费者，不保留双写、兼容列/状态映射或两套 Agent 循环。

设计中仍出现的旧措辞按已锁定决策处理：Kernel 是既有循环的端口泛化与两份适配器；开发阶段重建不导入旧 WAITING 历史；missed_count 是额外错过数；参数化并行上限不能被简单 NOT EXISTS 降为永远串行。相关实现 TASK 验证事实，运维 TASK 同步设计勘误。

## Task Index

| TASK | 可验证能力 | Depends |
|---|---|---|
| [TASK-001](#task-001) | 定义调度、等待、owner 与任务中心严格契约 | — |
| [TASK-002](#task-002) | 重建调度 schema 并校准 ORM 与隔离夹具 | TASK-001 |
| [TASK-003](#task-003) | 统一命令幂等、实体事件与终态事务 | TASK-002 |
| [TASK-004](#task-004) | 实现 IANA 墙钟 recurrence 与 DST 计划槽 | TASK-001 |
| [TASK-005](#task-005) | 配置调度限制并原子执行用户配额 | TASK-002, TASK-004 |
| [TASK-006](#task-006) | 接通 Task 凭据身份与 BACKGROUND 模型容量 | TASK-001, TASK-002 |
| [TASK-007](#task-007) | 实现 Task-owned ASYNC operation 与持久结果协议 | TASK-001, TASK-002, TASK-003, TASK-006 |
| [TASK-008](#task-008) | 新增 Task 产物写入与不可变版本 API | TASK-001, TASK-002, TASK-003 |
| [TASK-009](#task-009) | 泛化 Kernel 的挂起、事件与恢复端口 | TASK-001 |
| [TASK-010](#task-010) | 实现每次 Admission 与可重建 Snapshot v4 | TASK-001, TASK-005, TASK-006, TASK-007, TASK-008 |
| [TASK-011](#task-011) | 创建可预览且无副作用的 Schedule Draft | TASK-003, TASK-004, TASK-005, TASK-010 |
| [TASK-012](#task-012) | 原子确认 Draft 并创建或更新 Revision | TASK-003, TASK-004, TASK-005, TASK-011 |
| [TASK-013](#task-013) | 实现暂停、恢复及两档取消的完整状态矩阵 | TASK-003, TASK-004, TASK-007, TASK-010, TASK-012 |
| [TASK-014](#task-014) | 生成 Fire 并原子提交 Execution 与调度进度 | TASK-003, TASK-004, TASK-005, TASK-010, TASK-013 |
| [TASK-015](#task-015) | 改造 claim、lease 与三类外部等待 | TASK-003, TASK-005, TASK-014 |
| [TASK-016](#task-016) | 接通 Reminder、Skill 与现有立即任务执行链 | TASK-008, TASK-010, TASK-015 |
| [TASK-017](#task-017) | 以自然语言生成 Draft 并发送专用确认事件 | TASK-011, TASK-012, TASK-013 |
| [TASK-018](#task-018) | 切换 Runtime 生产路径并通过完整相关回归 | TASK-003, TASK-009, TASK-016, TASK-017 |
| [TASK-019](#task-019) | 实现完整 Worker Agentic ExecutionAdapter | TASK-006, TASK-007, TASK-008, TASK-018, TASK-010, TASK-016 |
| [TASK-020](#task-020) | 恢复 ASYNC 多 operation 并消除等待竞态 | TASK-007, TASK-015, TASK-019 |
| [TASK-021](#task-021) | 实现 Human 提问、回答、超时与双侧 CAS | TASK-003, TASK-013, TASK-019 |
| [TASK-022](#task-022) | 拆分独立 Delivery 并可靠推送 WeCom | TASK-003, TASK-008, TASK-013, TASK-016, TASK-021 |
| [TASK-023](#task-023) | 提供有界的 Worker Schedule/Fire/Execution 读面 | TASK-012, TASK-013, TASK-014, TASK-020, TASK-021, TASK-022 |
| [TASK-024](#task-024) | 挂载 WebChat 任务中心与安全下载/确认路由 | TASK-008, TASK-012, TASK-013, TASK-021, TASK-023 |
| [TASK-025](#task-025) | 接通 WeCom 用户确认与 Human 回复分流 | TASK-013, TASK-021, TASK-022, TASK-017 |
| [TASK-026](#task-026) | 接通 WebChat typed client、保留对话状态的 Tab 壳与 Draft 卡片 | TASK-024, TASK-017 |
| [TASK-027](#task-027) | 展示任务列表、详情、Fire 历史与版本产物 | TASK-026 |
| [TASK-028](#task-028) | 展示待处理 Human 并执行生命周期动作 | TASK-021, TASK-026, TASK-027 |
| [TASK-029](#task-029) | 适配 Console Schedule/Task API 与运营动作 | TASK-013, TASK-023 |
| [TASK-030](#task-030) | 更新 Console Schedule/Task 现有页面 | TASK-029 |
| [TASK-031](#task-031) | 实现有界保留清理并保护等待中的执行 | TASK-008, TASK-014, TASK-020, TASK-021, TASK-022, TASK-023 |
| [TASK-032](#task-032) | 登记指标、监督循环并补齐部署回滚操作 | TASK-005, TASK-014, TASK-020, TASK-021, TASK-022, TASK-024, TASK-031 |
| [TASK-033](#task-033) | 补齐真实全链路旅程及 CI 测试接线 | TASK-020, TASK-022, TASK-025, TASK-028, TASK-030, TASK-031, TASK-032 |

## 遗留测试归属（v1 面退役后必须有人认领）

下列文件仍在断言 TASK-002 之前的 v1 调度面（`scheduler.service.ScheduleService`、`compute_next_fire_at`、
`intent_key`、delivery 列、`WAITING`）。它们是**已知红**，不允许继续无人认领，也不允许靠改断言掩盖：
主责任任务必须把它们迁移到 v2 面，或写明「该事实已不存在」并删除对应用例。

| 文件 | 主责任 TASK | 处理口径 |
|---|---|---|
| `tests/agent_worker/test_task_defaults_from_settings.py` | TASK-016 | 迁到 v2 执行链的默认值/超时快照 |
| `tests/agent_runtime/test_capacity_webchat_clock_lease.py` | TASK-016 | 迁到 v2 Schedule 触发与容量路径 |
| `tests/agent_worker/test_task_lease_epoch.py` | TASK-020 | 迁到 `LeaseRenewal` 与 epoch 栅栏 |
| `tests/agent_worker/test_task_queries.py` | TASK-023 | 迁到 v2 读面（分页/过滤）；旧列断言删除并说明 |
| `tests/agent_worker/test_api.py`（`test_schedule_api_lifecycle` 等 v1 用例） | TASK-023 | 迁到 v2 读面与动作路由；`CANCEL_REQUESTED` 事件事实已不存在 |
| `tests/agent_worker/test_worker_lifecycle.py`（2 例断言 `CREATED`） | TASK-033 | 建单事件在 v2 是 `QUEUED`；收口前必须全绿 |
| `tests/agent_worker/test_schedule_writes.py` | TASK-029 | 创建/修改已由 Draft+确认取代：保留仍成立的事实（幂等、next_fire 计算）迁入 v2 套件，其余显式删除 |
| `tests/agent_worker/test_admin_schedules.py` | TASK-029 | 迁到 Console/管理面的 v2 状态与动作 |
| `tests/agent_worker/test_worker_metrics.py` | TASK-032 | 迁到 Fire/指标标签的 v2 口径 |
| `tests/console_platform/test_overview_api.py` | TASK-032 | 夹具仍 seed `intent_key`/`delivery_*`/`TaskSchedule.actor_user_id`；迁到 v2 列 |
| `tests/acceptance/overview/test_overview_acceptance.py` | TASK-032 | `waiting_tasks` 聚合改用 `WAITING_EXTERNAL/WAITING_HUMAN` |

## Spec Delta 与 Conventions 对齐

各 TASK 的 Delta 只修改所列 Rule，一条已有 Rule 只声明一次 update，before 为当前完整正文。新增 Rule ID 由目标 Spec 的 next_rule 分配，不预留/手写编号；Applied Deltas 记录由 CLI 保留。

全 TASK 完成并经用户验收后，归档复核还须按下表同步 `## Conventions`，删除/替换冲突的旧示例；规范正文只保留 Rules/Conventions/Avoid，不引入 H3，CLI 的 Applied Deltas 留档保留。当前 code-flow 的 Delta 只作用于 Rules，不会自动改 Conventions；下表是同次归档的明确文档工作，不能把 Delta 应用成功当作示例已更新。本轮不改已接受 Spec。

| 目标 Spec | 必须对齐的 Conventions 草稿（✅/❌） |
|---|---|
| persistence/harness-data | ✅ 同 Owner 业务 FK、跨 Owner/审计逻辑 UUID、outbox 指向真实 task_event_id，循环 FK 分步建立；❌ 旧 task_id+seq 复合事件 FK 或 Worker 直写 runtime 表。保留稳定注册表/tombstone 与不可变历史的完整唯一约束。 |
| time/harness-time | ✅ 规则在 Revision，显式 IANA，本地 gap 留事件并计槽、fold 第二映射只审计，ENDED 配合 end_reason；❌ 旧 schedule_type/cron 字段持有配置、把 MISSED 当 Schedule 当前状态、伪造 gap UTC Fire 或重复计错过数。保留 ISO/Console offset/DateTimeText 规范。 |
| snapshot/harness-snapshot | ✅ 按模式冻结依赖内 Skill 集合、完整工具 schema/模型池/MCP 非密定义/实际记忆块，恢复使用原 Snapshot；❌ “skills 恒为单个”、hash-only、整库 catalog、执行完成即删。保留 argv、canonical hash、实时凭据与交互式 Run 的原策略载体。 |
| memory/harness-memory | ✅ 交互式 Run 按既有规则刷新，定时 Task 使用准入冻结 READ_ONLY block，缺失读取明确 degraded；❌ 把逐轮刷新强加给 Task，或让任务写长期记忆。保留既有记忆确认、删除和提炼的权限/版本要求。 |
| worker/harness-worker | ✅ 新 task_type/WAITING_EXTERNAL/WAITING_HUMAN，operation→Schedule→Fire→tree→event 锁序、outbox event_id、独立 Delivery、started_at 判断、续接计 resume；❌ 旧 WAITING/Task delivery 列/固定旧键、Fire 二次 claim、续接耗 attempt、先锁 Child 后取 operation。保留有效 lease/deadline、BATCH 幂等、durable ack 与公平扫描要求；设置值/退避数不再写死在规范。 |
| api/harness-api | ✅ 用户确认 Draft 并生效同事务，重放先查 receipt，模型只产 Draft；❌ 模型工具自确认或确认后再靠模型另调创建。保留统一封套/catalog 和命令幂等。 |
| im/harness-im | ✅ user+authorized Agent 投影与指定版本产物下载，entry 只作元数据，NONE 无会话通知；❌ 用 entry 隔离任务或没有 Conversation 就拒绝任务产物。通道字段只留适配器。 |
| frontend/harness-frontend | ✅ 保留对话控制器的 Tab、schedule.draft.ready 卡调确认、独立 PendingPage；❌ 切 Tab 卸载 Run、复用 Run confirmation.required/resume、静态成功替代真实状态。 |
| e2e/harness-e2e | ✅ 真实跨服务任务中心旅程及 WebChat 零投递断言，未执行的浏览器结果待验证；❌ 旧主动 WebChat 定时推送 fixture 或组件通过冒充 E2E 通过。既有真实构建/隔离/域分层规则保留。 |

## 场景主责任

以下 44 个场景各指定一个主责任 TASK；其余支撑任务仍通过自己的 Scenarios 引用关联。主责任负责核对原场景期望与测试覆盖，实际人工执行状态仍为待验证。最后真实旅程 TASK 负责跨层接线及最终覆盖复核，不替代各任务的局部回归。

| 场景 | 主责任 TASK | 场景名称 |
|---|---|---|
| S-01 | TASK-025 | 通过 WeCom 创建固定提醒 |
| S-02 | TASK-027 | 通过 WebChat 创建 Skill 定时任务 |
| S-03 | TASK-019 | 创建 Agentic 定时任务 |
| S-04 | TASK-012 | 修改触发时间 |
| S-05 | TASK-013 | 暂停与恢复 |
| S-06 | TASK-013 | 取消任务 |
| S-07 | TASK-023 | 查看完整调度历史 |
| S-08 | TASK-014 | 多 Scheduler / 多 Worker 幂等 |
| S-09 | TASK-028 | WebChat 待处理 Human 问题 |
| S-10 | TASK-025 | WeCom Human 问题与回复 |
| S-11 | TASK-024 | WebChat 完全不投递 |
| S-12 | TASK-024 | MSSWWEB 契约预留（任务中心另立需求，本需求不落地） |
| S-13 | TASK-030 | Console 查看与运营操作 |
| S-14 | TASK-012 | 创建幂等与 Draft 重放 |
| S-15 | TASK-019 | Agentic 工具越权 |
| S-16 | TASK-010 | Agentic 记忆只读 |
| S-17 | TASK-020 | Agentic 调用 ASYNC Skill |
| E-01 | TASK-011 | 缺少时区或参数 |
| E-02 | TASK-014 | Skill 删除/禁用 |
| E-03 | TASK-014 | 重复 claim / 重复 Fire |
| E-04 | TASK-012 | Draft 幂等冲突 |
| E-05 | TASK-012 | 更新竞态 |
| E-06 | TASK-013 | 暂停与触发竞态 |
| E-07 | TASK-021 | Human 超时 |
| E-08 | TASK-025 | 多个 OPEN Human 问题 |
| E-09 | TASK-022 | WeCom 路由失效 |
| E-10 | TASK-014 | Fire 时工具缺失/撤权 |
| E-11 | TASK-024 | WebChat 误投递 |
| E-12 | TASK-029 | Console 创建入口 |
| E-13 | TASK-014 | ONCE 准入失败 |
| E-14 | TASK-012 | 模型同轮自动确认 |
| E-15 | TASK-021 | Human 回答与取消/超时竞态 |
| B-01 | TASK-004 | DST 边界 |
| B-02 | TASK-014 | 错过区间 |
| B-03 | TASK-014 | 结束条件 |
| B-04 | TASK-005 | 配额与最小间隔 |
| B-05 | TASK-015 | QUEUE 并发 |
| B-06 | TASK-015 | ALLOW_PARALLEL 上限 |
| B-07 | TASK-017 | 多客户拆分 |
| B-08 | TASK-019 | Agentic deadline / budget |
| B-09 | TASK-027 | 长结果与产物 |
| B-10 | TASK-031 | 历史保留与分页 |
| B-11 | TASK-024 | 无 Conversation 的任务产物下载 |
| B-12 | TASK-012 | 排队旧 Execution 更新取消 |

<!-- 以下每个 TASK 使用显式路径 + TASK ID 绑定；禁止拆成独立任务文件。 -->

<a id="task-001"></a>

## TASK-001: 定义调度、等待、owner 与任务中心严格契约

- **Status**: done
- **Depends**:
- **Source**: scheduled-task-v2.backend.design.md §3.1.5、§3.3、§3.4；scheduled-task-v2.frontend.design.md §3.4
- **Scenarios**: S-01, S-02, S-03, S-07, S-12, S-17, E-13, E-14, B-11
- **Spec-Refs**: api/harness-api, worker/harness-worker, snapshot/harness-snapshot, persistence/harness-data, secret/harness-secret, test/harness-test
- **Paths**: packages/contracts/src/muad_contracts/**, packages/api-kit/src/muad_api/error_codes.py, config/api-messages.yaml, tests/contracts/test_scheduled_v2_contracts.py, tests/agent_worker/test_task_contracts.py
- **E2E**:
- **Skip-Tests**: false
- **Skip-Reason**:

### Tests

```json
[
  {
    "id": "related",
    "argv": [
      "uv",
      "run",
      "python",
      "-m",
      "pytest",
      "-q",
      "tests/contracts/test_scheduled_v2_contracts.py",
      "tests/agent_worker/test_task_contracts.py"
    ],
    "timeout": 600,
    "parallel": false
  }
]
```

### Description

先固定所有消费者共用的类型与错误语义；Schedule 三模式与内部 BATCH 分开，等待种类、可信确认、Task owner、Snapshot 判别联合和分页 DTO 都由 contracts 持有。此任务不暴露未实现的业务端点。

### Checklist

- [x] 定义 Schedule/Fire/Execution/Human 状态、CancelScope、wait_kind 与模式专属必填/互斥字段；去掉旧 WAITING、FAILED_CONFIG、cancel_running 和 intent_key 协议（Schedule 侧 `intent_key` 已删；立即任务 `CreateTaskRequest.intent_key` 与其 ORM 列/投递文案一起在 TASK-002/TASK-016 移除）。
- [x] 定义 Draft CREATE/UPDATE、base_config_version、完整 sha256: hash、确认请求/回执、动作与 PendingPage/Badges/FireHistoryPage；非法额外字段和非严格 JSON 显式拒绝。
- [x] 定义 RUN/TASK owner 联合及 Task 容量、operation/result/receipt、凭据 execution_ref、artifact 写入/版本/下载类型；owner ID 不复用 ToolOperation.task_id。
- [x] 定义按模式判别的 Snapshot v4：REMINDER 无 Skill、SKILL 精确一个、AGENTIC 冻结依赖内全部 Skill；budgets、MCP 完整非密定义、模型池/摘要模型、记忆块均可验证。
- [x] 新增错误码同步 ErrorCode 与消息 catalog；覆盖非法状态组合、hash 格式、wait payload、多 operation、owner XOR、分页与版本冲突契约。

### Spec Delta

```json
[]
```

### Log

- [2026-11-15] created (draft)
- [2026-11-15T05:33:31.492Z] started tests=2de5d14f7d5f
- [2026-11-15] 新增 `muad_contracts/scheduling.py`：Draft/确认/动作、Fire 历史+DST 事件、Execution 详情与 `operations[]` 水位、Pending/Badges、Task owner（容量/工具操作/产物写入版本）、Snapshot v4 判别联合。枚举换成 v2 词表（`TaskStatus` 拆 `WAITING_EXTERNAL/WAITING_HUMAN`、`ScheduleStatus` 收为 `ENDED`+`end_reason`），`ModelCallOwner` 扩 `TASK`、`RuntimeExecutionRef` 扩 `type=TASK`。
- [2026-11-15] 新增 24 个错误码，同步 `ErrorCode` 与 `config/api-messages.yaml`（`tests/test_error_catalog.py` 双向断言绿）。
- [2026-11-15] 过渡事实：Worker 侧 `str(ScheduleStatus.ENDED)`/`WAITING_EXTERNAL` 只是让词表可导入的最小改名，调度语义与断言由 TASK-014 重写；`task.task_schedule.intent_key` 列暂以 `name` 填充，随 TASK-002 重建删除。

### Verification

- 尚未执行；此 TASK 的声明测试、业务场景均未宣称通过。

<a id="task-002"></a>
- 2026-11-15T05:41:29.178Z PASS commands=1/1 executed=1 deduped=0 elapsed=25507ms tests=2de5d14f7d5f head=e92e1460b070 dirty=1
  - related PASS exit=0
## TASK-002: 重建调度 schema 并校准 ORM 与隔离夹具

- **Status**: done
- **Depends**: TASK-001
- **Source**: scheduled-task-v2.backend.design.md §3.3、§4.4
- **Scenarios**: S-07, S-08, S-14, S-17, E-05, E-15, B-11, B-12
- **Spec-Refs**: persistence/harness-data, time/harness-time, test/harness-test
- **Paths**: migrations/**, apps/agent-worker/src/muad_agent_worker/infrastructure/models/**, apps/agent-runtime/src/muad_agent_runtime/infrastructure/models/**, apps/console-platform/backend/src/muad_console_platform/infrastructure/models/**, tests/agent_worker/conftest.py, tests/agent_runtime/conftest.py, tests/migrations/**
- **E2E**:
- **Skip-Tests**: false
- **Skip-Reason**:

### Tests

```json
[
  {
    "id": "related",
    "argv": [
      "uv",
      "run",
      "python",
      "-m",
      "pytest",
      "-q",
      "tests/migrations/test_scheduled_v2_migration.py",
      "tests/migrations/test_scheduled_v2_schema.py",
      "tests/agent_worker/test_task_schema_parity.py",
      "tests/agent_worker/test_submission_schema_parity.py"
    ],
    "timeout": 600,
    "parallel": false
  }
]
```

### Description

用下一个实际可用的 Alembic revision 一次性重建 task 调度表，保留 workspace 两表；Runtime 表原位增加 Task owner。ORM、迁移和测试清理顺序同构，不导入旧调度历史。

### Checklist

- [x] 先断开循环 FK，再按真实依赖 drop/create；恢复 Schedule↔Revision、Fire↔Execution 引用；保留 workspace/workspace_lease 的行、约束和租约。
- [x] 补 Draft、Revision、Fire、Checkpoint、Human、Delivery/Attempt、新事件表；保留 source operation、submission、outbox 能力，outbox 改 task_event_id 单列同 Owner FK。
- [x] 所有产品表使用标准四列与 timestamptz；建立 occurrence/root execution、OPEN interaction、confirmation ref、子任务等唯一约束及 due/FIFO/等待查询索引。
- [x] Runtime operation/inbox/lease 的 Run 字段可空，加入 owner XOR/按 owner 唯一与 receipt kind 逻辑引用；artifact 保持 run_id/task_id XOR；不建跨 Owner FK。
- [x] 配置 CAS 与调度进度分离；draft_hash 能容纳契约完整格式；不保留 current_revision_no、pending_operation_id、旧 delivery 列或同名唯一。
- [x] 迁移只读显式 ini；用独立临时库验证前一 revision→head、head 重放、约束/索引/ORM parity、破坏性 downgrade 明确拒绝；更新夹具避免旧列清理把后续测试全部卡住。

### Spec Delta

```json
[
  {
    "id": "DELTA-001",
    "op": "update",
    "target": "persistence/harness-data",
    "ref": "RULE-data-001",
    "before": "产品表统一 `id/is_deleted/create_time/update_time`；普通软删除实体用 partial unique `WHERE is_deleted=false`，稳定身份注册表 `runtime.user_memory` / `memory_scope` 的完整唯一键包含 tombstone，append-only history/receipt/job 保持 `is_deleted=false` 且使用完整唯一约束；时间统一 `timestamptz`；同 Owner Schema 的业务归属用物理 FK，跨 Owner Schema 与需跨清理保留的审计来源引用用逻辑 UUID；JSON 配置用 `jsonb`，关键查询字段必须有物理列。",
    "rule": "产品表统一 id/is_deleted/create_time/update_time，时间统一 timestamptz，JSON 配置使用 jsonb；普通软删除实体以未删除行建立部分唯一，稳定身份注册表及不可变历史、回执按身份完整唯一。业务归属在同 Owner Schema 内使用物理外键，跨 Owner Schema、操作者及需独立保留的审计来源使用逻辑 UUID；多态审计引用由写入方校验实体归属。同 Owner 的结果发件引用实际事件身份，不能依赖已经失效的任务与事件序号复合关系。关键查询字段、互斥 owner 和业务唯一性必须由共享契约对应的物理列、约束及索引承载。",
    "evidence": "scheduled-task-v2.backend.design.md §3.3.9、§3.3.11、§3.3.12、§4.4；TASK-002 的迁移与 schema parity 回归。"
  }
]
```

### Log

- [2026-11-15] created (draft)
- [2026-11-15T05:41:45.403Z] started tests=fa927165a526
- [2026-11-15] Alembic 0050 一次性重建：drop 0049 的 7 张调度表（workspace/lease 保留）后按 §3.3 建 11 张新表，循环 FK（Schedule↔Revision、Fire↔Execution）先建列后补约束；outbox 改 `task_event_id` 单列同 Owner FK；runtime 侧补 `owner_type`/`task_execution_id` 与四选一 `num_nonnulls`，inbox 用 `receipt_event_type` + 逻辑 UUID。
- [2026-11-15] 本地开发库已随之迁到 0050（迁移前 `pg_dump` 快照在 /tmp/muad_task_schema_pre_0050.sql）；旧调度历史按设计不导入。
- [2026-11-15] 夹具：`tests/agent_worker/conftest.py` 的清库顺序改为子行→引用行→父行，多态 `task_event` 按租户整体清理；`test_task_schema_parity.py` 覆盖 11 张表。

### Verification

- 尚未执行；此 TASK 的声明测试、业务场景均未宣称通过。

<a id="task-003"></a>
- 2026-11-15T05:48:23.374Z PASS commands=1/1 executed=1 deduped=0 elapsed=24005ms tests=fa927165a526 head=8f6fb65dffdd dirty=1
  - related PASS exit=0
## TASK-003: 统一命令幂等、实体事件与终态事务

- **Status**: done
- **Depends**: TASK-002
- **Source**: scheduled-task-v2.backend.design.md §3.3.9、§3.3.12、§3.5.1、§3.5.10
- **Scenarios**: S-06, S-07, S-08, S-14, E-03, E-04, E-15
- **Spec-Refs**: api/harness-api, persistence/harness-data, worker/harness-worker, logging/harness-log, test/harness-test
- **Paths**: apps/agent-worker/src/muad_agent_worker/application/**, tests/agent_worker/test_scheduled_v2_transactions.py, tests/agent_worker/test_submission_idempotency.py, tests/agent_worker/test_task_events.py, tests/agent_worker/test_runtime_operation_races.py
- **E2E**:
- **Skip-Tests**: false
- **Skip-Reason**:

### Tests

```json
[
  {
    "id": "related",
    "argv": [
      "uv",
      "run",
      "python",
      "-m",
      "pytest",
      "-q",
      "tests/agent_worker/test_scheduled_v2_transactions.py",
      "tests/agent_worker/test_submission_idempotency.py",
      "tests/agent_worker/test_task_events.py",
      "tests/agent_worker/test_runtime_operation_races.py"
    ],
    "timeout": 600,
    "parallel": false
  }
]
```

### Description

提供后续创建、动作、回答与终态共用的原子写入基础。事件是审计，实体行是状态权威；事件失败时业务变更与 outbox 同时回滚。

### Checklist

- [x] task_submission 接纳 tasks、schedule-drafts、schedule-confirm、schedule-actions、execution-cancel、human-respond；指纹含 endpoint/tenant/actor/目标与动作载荷。
- [x] 先 advisory lock 再重放校验与写入，partial unique 兜底；同键不同 actor/目标/参数冲突，重放读取首次持久化响应。
- [x] 事件 seq 在实体行锁或实体 advisory lock 内分配；并发写序号不重复，occurred_at 与入库时间分离。
- [x] 扩展统一锁入口：runtime_operation→Schedule→Fire→root→parent→child→event/outbox；立即任务沿用 operation→tree；不先更新 Child 再反向拿 operation。
- [x] 统一终态写状态、finished_at、TaskEvent、JOIN outbox 和 PUSH 投递意图；先 flush 实际事件再写 outbox FK，Child 不发布根结果。
- [x] 覆盖不同实体并发、同实体并发、事务注入失败、取消先到、迟到 lease、JOIN/DETACH/Child 行为，使用真实 PG 锁和约束。

### Spec Delta

```json
[]
```

### Log

- [2026-11-15] 收尾补正（协调侧）：`api/runtime_operations.py` 的取消端点仍在用旧签名 `submission_fingerprint(endpoint, body)` 且传字符串端点——TASK-003 改口径后这里每次调用都会 TypeError。已改用 `SubmissionEndpoint.EXECUTION_CANCEL` + 关键字指纹（endpoint/tenant/actor/目标/载荷）。
- [2026-11-15] 收尾补正：`tests/contracts/test_runtime_strict_json.py` 的 `test_b02_pg_replay_conflicts_and_unique_scope`仍按旧签名 `submission_fingerprint(endpoint, request)` 调用，并把 partial unique 落败当成裸 `IntegrityError`。本任务的 API 变更后它成了无人认领的红灯，已由协调侧迁到新口径（关键字指纹 + 落败者按指纹判定重放/冲突），18 passed。
- [2026-11-15] created (draft)
- [2026-11-15T05:52:33.430Z] started tests=9f4e218971ce
- [2026-11-15] `task_submission` 收敛为六类 endpoint 短标识（`tasks`/`schedule-drafts`/`schedule-confirm`/`schedule-actions`/`execution-cancel`/`human-respond`）；`admit()` = `pg_advisory_xact_lock` → 重放校验，`record_in()` 用 SAVEPOINT 把 partial unique 兜底翻成「重放 / `IDEMPOTENCY_MISMATCH`」而不是把 IntegrityError 抛给调用方；指纹显式含 endpoint/tenant/actor/目标 id/动作载荷。
- [2026-11-15] `task_event` 改多态：`EntityType` + 每实体事件目录（§3.5.10）；seq 在实体行锁（SCHEDULE/FIRE/EXECUTION/HUMAN/DELIVERY）或实体 advisory 锁（ARTIFACT/ASYNC）内分配，下界与唯一键一样按 tenant 收敛（首版漏了 tenant 谓词，被新增的跨租户用例抓出并修正）。
- [2026-11-15] 终态事务：EXECUTION 事件先 flush，outbox 改 `task_event_id` 单列 FK；PUSH 投递意图按来源 Revision 的 `delivery_intent` 在同一事务建 `task_delivery`；删除旧的 WEBCHAT→Runtime 收件箱外置分支（v2 由任务中心 pull，§2.3.6/§3.5.9）。
- [2026-11-15] 锁入口 `lock_task_tree` 扩到 operation→Schedule→Fire→root→parent；立即任务仍是 operation→tree，所有路径都先取 operation 锁再碰执行行。
- [2026-11-15] 事件目录里没有 CANCEL_REQUESTED / FAN_OUT / FAN_IN：RUNNING 的协作取消只落 `cancel_requested` 列（终态 CANCELLED 事件由持 lease 的 Worker 写），fan-in 聚合明细进终态事件 payload。
- [2026-11-15] 测试迁移：`test_submission_idempotency` 下沉到台账服务层——`/internal/tasks` 的 HTTP 接线仍依赖 `TaskService.create`（TASK-016）故属其他 TASK；`test_runtime_operation_races` 收敛到终态事务/operation 墓碑/outbox 派发，worker/scheduler 收尾接线（success/failure/deadline 的调用方）由 TASK-015 的套件覆盖。
- [2026-11-15] 推迟项：`apps/agent-worker/src/muad_agent_worker/api/admin_results.py` 的 runtime-result 重试端点仍调用 v1 `append_event(task_id=...)` / `TaskEventType.DELIVERY_RETRY`，其 owner（TASK-013/023）需迁移；`application/batch_fanout.py` 的 `external_ref_json`/FAN_OUT 事件同样留给 TASK-015。
- [2026-11-15] 共享夹具 `tests/agent_worker/helpers.py`、`tests/async_tool_helpers.py` 去掉了执行行已删除的列（intent_key/delivery_*/execution_mode）。

### Verification

- 尚未执行；此 TASK 的声明测试、业务场景均未宣称通过。

<a id="task-004"></a>
- 2026-11-15T06:02:31.003Z PASS commands=1/1 executed=1 deduped=0 elapsed=32435ms tests=9f4e218971ce head=6665e3589171 dirty=1
  - related PASS exit=0
## TASK-004: 实现 IANA 墙钟 recurrence 与 DST 计划槽

- **Status**: done
- **Depends**: TASK-001
- **Source**: scheduled-task-v2.backend.design.md §2.3.7、§3.5.2、§3.5.11
- **Scenarios**: S-01, S-04, E-01, B-01, B-02, B-03
- **Spec-Refs**: time/harness-time, worker/harness-worker, test/harness-test
- **Paths**: packages/contracts/src/muad_contracts/tasks.py, apps/agent-worker/src/muad_agent_worker/scheduler/**, tests/agent_worker/test_scheduled_v2_recurrence.py
- **E2E**:
- **Skip-Tests**: false
- **Skip-Reason**:

### Tests

```json
[
  {
    "id": "related",
    "argv": [
      "uv",
      "run",
      "python",
      "-m",
      "pytest",
      "-q",
      "tests/agent_worker/test_scheduled_v2_recurrence.py"
    ],
    "timeout": 600,
    "parallel": false
  }
]
```

### Description

交付纯计算 recurrence：CRON/ONCE、IANA、本地计划槽与明确的 DST 决策；返回可供 Scheduler 原子落库的正常 occurrence、DST 事件、计数变化和下一时间，不在纯函数内写库或联网。

### Checklist

- [x] 时区必填且 ZoneInfo 校验；所有外部时间必须带时区；用户时区缺失返回澄清（`SCHEDULE_TIMEZONE_REQUIRED` 在 TASK-011 的 Draft 入口抛），引擎侧不做服务器本地时区回落。
- [x] gap=SKIP 不伪造 Fire，产生本地日期/时刻审计描述并消耗一个计划槽；fold=FIRST 只选第一次映射，第二次审计且不重复计数。
- [x] misfire 聚合区间，missed_count 只计额外错过数；处理次数使用 1+missed_count；覆盖跨 DST、until 和 max_occurrences 组合。
- [x] ONCE 成功、错过均有明确终止原因（`COMPLETED`），准入失败由 TASK-014 的 Admission 分支写 `ADMISSION_FAILED`；恢复从 now 后计算，不补暂停期。
- [x] execution_key 的时间规范化固定 UTC、微秒与 Z；等价 offset 和不同序列化输入生成同一键。


### Spec Delta

```json
[
  {
    "id": "DELTA-001",
    "op": "update",
    "target": "time/harness-time",
    "ref": "RULE-time-001",
    "before": "数据库时间存储遵循 RULE-data-001（统一 `timestamptz`）；Console 展示统一 `YYYY-MM-DD HH:mm:ss`（出参必须带 offset）；调度时区使用 IANA 时区（如 `Asia/Shanghai`）。",
    "rule": "数据库时间统一遵循 RULE-data-001；内部 API 输出带时区的 ISO8601，Console 面向前端的时间由共享格式化器输出 offset，前端统一经 DateTimeText 渲染。调度规则与支持的 recurrence 类型由共享调度契约持有，IANA 时区必须显式提供并验证，不得使用服务器本地时区兜底。调度按本地计划槽计算：不存在的墙钟时刻按策略留调度事件，重复墙钟时刻只采用策略允许的映射；不得伪造 UTC Fire。计划槽计数、聚合错过区间的额外次数与结束条件必须一致；已终止的单次计划只能重新创建，不能恢复原 occurrence。",
    "evidence": "scheduled-task-v2.backend.design.md §2.3.7、§3.5.2、§3.5.11；B-01/B-02/B-03 与 TASK-004 的可控时钟回归。"
  }
]
```

### Log

- [2026-11-15] 新增 `scheduler/recurrence.py`：纯计算的本地计划槽引擎（不读时钟、不写库）。`next_fire_at` 只是扫描指针，只有 FIRE 计划产生 Fire；gap 槽与 fold 第二次以 `dst_events` 返回，供 Scheduler 落 SCHEDULE 事件；`occurrence_count` 计本地计划槽。
- [2026-11-15] 口径确定：misfire 的过期边界是 `now - grace_sec`，边界内的槽不算错过；错过区间的槽数按本地槽逐个走，不按 UTC 秒数除法（跨 DST 会算错）。
- [2026-11-15] created (draft)
- [2026-11-15T05:48:30.160Z] started tests=4371bebcae03

### Verification

- 尚未执行；此 TASK 的声明测试、业务场景均未宣称通过。

<a id="task-005"></a>
- 2026-11-15T05:50:24.622Z PASS commands=1/1 executed=1 deduped=0 elapsed=3242ms tests=4371bebcae03 head=0eba580f4728 dirty=1
  - related PASS exit=0
## TASK-005: 配置调度限制并原子执行用户配额

- **Status**: done
- **Depends**: TASK-002, TASK-004
- **Source**: scheduled-task-v2.backend.design.md §3.3.3、§3.5.11；scheduled-task-v2.frontend.design.md §3.6
- **Scenarios**: E-01, B-04, B-06, B-08, B-10
- **Spec-Refs**: worker/harness-worker, snapshot/harness-snapshot, persistence/harness-data, secret/harness-secret, test/harness-test
- **Paths**: packages/contracts/src/muad_contracts/platform_settings.py, packages/common/src/muad_common/**, apps/console-platform/backend/src/muad_console_platform/application/platform_settings*, apps/agent-worker/src/muad_agent_worker/application/**, tests/contracts/test_scheduled_v2_settings.py, tests/agent_worker/test_scheduled_v2_quota.py
- **E2E**:
- **Skip-Tests**: false
- **Skip-Reason**:

### Tests

```json
[
  {
    "id": "related",
    "argv": [
      "uv",
      "run",
      "python",
      "-m",
      "pytest",
      "-q",
      "tests/contracts/test_scheduled_v2_settings.py",
      "tests/agent_worker/test_scheduled_v2_quota.py"
    ],
    "timeout": 600,
    "parallel": false
  }
]
```

### Description

把 Schedule 数、间隔、日触发、Agentic 运行/并发、Snapshot 大小、确认时效和保留策略纳入现有设置所有者，在操作边界冻结，执行热路径不重新取设置。

### Checklist

- [x] 新增严格的设置 schema/catalog/projection；配置值以设置所有者为准，设计和 Spec 不复制默认数字。
- [x] Draft 静态校验与实际创建/Fire/claim 的配额复检分开；多 Schedule 并发以 tenant+user 原子锁或日桶原子计数，不只锁单条 Schedule。
- [x] 配额失败在 Draft 明确拒绝，在 Fire 记录 SKIPPED(QUOTA)；拒绝与计数提交同一事务，回滚不泄漏配额。
- [x] 覆盖两个 Scheduler、两个不同 Schedule 同用户抢最后额度、不同租户独立、自然日边界与最小间隔；不使用全库 count。
- [x] 等待 Human/External 与续接不被误算为新的业务尝试或新日触发；设置更改只影响后续操作边界。

### Spec Delta

```json
[]
```

### Log

- [2026-11-15] `tests/agent_worker/test_task_defaults_from_settings.py` 已从本任务的声明测试移出（它因 TASK-002 的执行链列变更而 7 failed，与配额/设置无关），改由 TASK-016 承接；主仓已核对该文件当时确实红。
- [2026-11-15] created (draft)
- [2026-11-15] 契约层新增 `ScheduleSettings`（9 叶子：Schedule 数/最小间隔/日 Fire/日 Agentic/并发 Agentic/Fire 与执行保留天数/Snapshot 上限/确认时效），范围只写在字段元信息；`parse_platform_settings` 纳入 `_GROUPS`，跨字段联动要求并发 Agentic 不得超过日 Agentic 上限。设计里的无单位 `confirmation_ttl` 落为 `confirmation_ttl_sec`（与既有 `*_sec` 口径一致）。
- [2026-11-15] Console catalog 增加 `schedule` 分组（`next_operation`，边界生效），范围/单位从 schema 派生，effect 词条区分 Draft 边界与每次 Fire；zh-CN/en-US 词条补齐。
- [2026-11-15] 新增 `application/schedule_quota.py`：Draft 边界 `ensure_schedule_capacity`（终态不占名）+ `ensure_min_interval`；Fire 边界 `admit_fire` 在写 Fire 的同一事务内复检日 Fire/日 Agentic/并发 Agentic，超限由 `record_quota_skip` 落 `SKIPPED(skip_reason=QUOTA)`。用户维度用 `pg_advisory_xact_lock(hashtextextended('muad:schedule-quota:<tenant>:<user>'))` 串行化，日计数在锁内按 `(tenant_id, owner_user_id)` 查询（不是全库 count）；锁在 Schedule 行锁之内获取。
- [2026-11-15] 口径：日触发按 occurrence 的 `scheduled_at` 落在 Schedule Revision 的 IANA 本地自然日窗口内计数；`SKIPPED`/`CANCELLED` Fire 不算业务触发；并发只计占槽的 `QUEUED`/`RUNNING`，`WAITING_HUMAN`/`WAITING_EXTERNAL` 不占槽也不新增日触发。FIRE `SKIPPED` 审计事件由共享实体事件写入方在调用方事务内追加（TASK-003/014 接线），本模块不另建事件写入。
- [2026-11-15] 并发用例以两个独立会话真实抢同一份额度；做过无锁变异验证（注释掉 advisory 锁后两个并发用例立即变红），证明断言不是单会话自证。
- [2026-11-15] 声明测试移除 `tests/agent_worker/test_task_defaults_from_settings.py`：该文件在 `main` 基线即 7 failed（`TaskExecution.intent_key` 等 TASK-003/016 未落地的执行链），与本任务的设置/配额无关，不做越界修复。
- [2026-11-15T05:52:36.877Z] started tests=02b5f560b3f0

### Verification

- 尚未执行；此 TASK 的声明测试、业务场景均未宣称通过。

<a id="task-006"></a>
- 2026-11-15T06:07:42.636Z PASS commands=1/1 executed=1 deduped=0 elapsed=14885ms tests=e5ec7194107b head=6665e3589171 dirty=1 tests-changed
  - related PASS exit=0
## TASK-006: 接通 Task 凭据身份与 BACKGROUND 模型容量

- **Status**: done
- **Depends**: TASK-001, TASK-002
- **Source**: scheduled-task-v2.backend.design.md §3.1.5、API-29、§3.5.6、§4.4
- **Scenarios**: S-03, S-15, S-17, E-10, B-08
- **Spec-Refs**: auth/harness-auth, model/harness-model, secret/harness-secret, persistence/harness-data, snapshot/harness-snapshot, test/harness-test
- **Paths**: apps/agent-runtime/src/muad_agent_runtime/api/model_capacity.py, apps/agent-runtime/src/muad_agent_runtime/application/model_capacity*, apps/agent-runtime/src/muad_agent_runtime/infrastructure/model_capacity_store.py, apps/console-platform/backend/src/muad_console_platform/application/runtime_credentials.py, apps/console-platform/backend/src/muad_console_platform/api/**, apps/agent-worker/src/muad_agent_worker/infrastructure/**, tests/agent_runtime/test_scheduled_v2_task_capacity.py, tests/console_internal/test_scheduled_v2_task_credentials.py
- **E2E**:
- **Skip-Tests**: false
- **Skip-Reason**:

### Tests

```json
[
  {
    "id": "related",
    "argv": [
      "uv",
      "run",
      "python",
      "-m",
      "pytest",
      "-q",
      "tests/agent_runtime/test_scheduled_v2_task_capacity.py",
      "tests/console_internal/test_scheduled_v2_task_credentials.py",
      "tests/agent_runtime/test_capacity_webchat_probe_capacity_api.py",
      "tests/agent_runtime/test_capacity_webchat_background_pool.py"
    ],
    "timeout": 600,
    "parallel": false
  }
]
```

### Description

实现新增的 Task 容量预留/续租/结算 HTTP 面与凭据 TASK execution_ref，复用 Runtime 容量所有权；现有 Probe API 不充当 Task API。

### Checklist

- [x] 只允许内部服务身份请求；从可信 Execution owner 校验 tenant/actor/agent，拒绝仅靠 body 伪造归属；不创建 Run。
- [x] Task owner 进入 model_call_lease 四选一约束，workload=BACKGROUND；冻结模型池和 summary model 复用同一容量协议。
- [x] 预留/续租/结算携带 owner 与 lease fence；过期、重复结算、token usage 与并发上限有明确结果，失约停止本地模型请求。
- [x] 凭据实时解析并仅在内存使用；不将 api_key、auth_secret 写入 Snapshot、hash、事件或错误响应。
- [x] 验证 RUN/MEMORY_JOB/PROBE 原有 owner 行为不变，Task 的跨租户/用户/Agent 与 lease 伪造均拒绝。

### Spec Delta

```json
[
  {
    "id": "DELTA-001",
    "op": "add",
    "target": "persistence/harness-data",
    "rule": "共享执行基础设施必须使用显式 owner 联合与互斥归属约束；调用方携带服务身份，由资源所有者复核租户、操作者和 Agent，不能把提交结果的 Task 标识复用为 operation owner。Runtime 持有其容量、异步操作、结果收件箱及产物的写入权，Worker 经所有者内部 API 使用 Task 能力；跨 Owner 引用为逻辑身份，禁止直接写入其它 Owner 表。",
    "evidence": "scheduled-task-v2.backend.design.md §3.1.5、§4.4；TASK-006/TASK-007/TASK-008 的 owner 与授权契约。"
  }
]
```

### Log

- [2026-11-15] created (draft)
- [2026-11-15T06:18:31.280Z] started tests=a0d73e9f73c3
- [2026-11-15] API-29 落在 `api/model_capacity.py` 的 `TaskCapacityPort`：`/internal/model-capacity/task-reservations`（+`/{id}/renew|settle`），与 Probe 端口共用同一个 `ModelCapacityService`，owner 类型各自固定、不互相充当对方 API；响应只有 reservation_id/epoch/lease_until（重放与结算同口径）。
- [2026-11-15] 契约只补了缺的四个 Task 线协议 DTO（`TaskCapacityGrant/TaskCapacityRelease/TaskLeaseRenewal/TaskCallSettlement`），owner/请求沿用 TASK-001 的 `TaskCapacityOwner`/`TaskCapacityReservationRequest`；结算形状复用 `_require_settlement_shape`，不复制不变量。
- [2026-11-15] 容量归属校验分工：Runtime 按 `task.task_execution` 只读复核 **tenant**（跨租户 404）与 **actor**（`X-Actor-User-Id` 与 Execution 不符即 `TASK_ACCESS_DENIED`，缺头也拒绝）；**agent** 由 Console 凭据解析按 Execution 冻结的 `model.model_id` 复核（跨模型/跨 agent 即 `TASK_ACCESS_DENIED`）。Execution 的 agent 只由该行唯一确定，不是线协议字段，因此没有可伪造的 agent 声明。
- [2026-11-15] `model_call_lease.owner_type` 现在与四选一 owner 列同源写库（`insert_lease` 写 `request.owner.kind`），`_require_same_attempt` 比较四项 owner 与 `owner_type`；`_owner_reference` 对四类 owner 显式分支，不再用"最后一个分支兜底"。
- [2026-11-15] 冻结引用在预占前与当前控制面核对（成员/版本/`key_resource_id`/额度组），轮换或换成员即 `MODEL_CREDENTIAL_CHANGED`，不拿新 Key 消费旧槽位；租约执行方身份由 Execution 派生（`task:{execution_id}`），线协议无法声明。
- [2026-11-15] Worker 侧新增 `infrastructure/task_capacity_client.py`：只发服务身份+租户头+操作者头，`MODEL_CALL_LEASE_LOST` → `CapacityLeaseLost`（调用方停手）、`MODEL_CAPACITY_UNAVAILABLE` → `CapacityUnavailable`（让路），其余/传输失败一律"无结论"。
- [2026-11-15] 测试：`tests/agent_runtime/test_scheduled_v2_task_capacity.py`（14，真实 uvicorn + 真实 PG 四选一约束 + 真实 Worker 客户端）、`tests/console_internal/test_scheduled_v2_task_credentials.py`（7，真实 HTTP + 真实 `task.task_execution`），并在同批回归 Probe/后台池两套既有用例与 Contract/Console RUN 链。

### Verification

- 尚未执行；此 TASK 的声明测试、业务场景均未宣称通过。

<a id="task-007"></a>
- 2026-11-15T06:29:53.139Z PASS commands=1/1 executed=1 deduped=0 elapsed=31123ms tests=a0d73e9f73c3 head=a0a06fc600af dirty=1
  - related PASS exit=0
## TASK-007: 实现 Task-owned ASYNC operation 与持久结果协议

- **Status**: done
- **Depends**: TASK-001, TASK-002, TASK-003, TASK-006
- **Source**: scheduled-task-v2.backend.design.md §3.1.5、§3.2.4、API-30、§3.5.1、§4.4
- **Scenarios**: S-17, E-03, E-15, B-08
- **Spec-Refs**: worker/harness-worker, api/harness-api, snapshot/harness-snapshot, auth/harness-auth, persistence/harness-data, test/harness-test
- **Paths**: apps/agent-runtime/src/muad_agent_runtime/api/**, apps/agent-runtime/src/muad_agent_runtime/application/async_tools/**, apps/agent-runtime/src/muad_agent_runtime/application/tool_result_receipts.py, apps/agent-worker/src/muad_agent_worker/application/runtime_operations.py, apps/agent-worker/src/muad_agent_worker/api/runtime_operations.py, tests/agent_runtime/test_scheduled_v2_task_operations.py, tests/agent_runtime/test_scheduled_v2_task_inbox.py
- **E2E**:
- **Skip-Tests**: false
- **Skip-Reason**:

### Tests

```json
[
  {
    "id": "related",
    "argv": [
      "uv",
      "run",
      "python",
      "-m",
      "pytest",
      "-q",
      "tests/agent_runtime/test_scheduled_v2_task_operations.py",
      "tests/agent_runtime/test_scheduled_v2_task_inbox.py",
      "tests/agent_worker/test_task_operation_identity.py",
      "tests/agent_worker/test_runtime_operation_races.py"
    ],
    "timeout": 600,
    "parallel": false
  }
]
```

### Description

补齐提交/查找/查询结果/取消及 Task receipt 契约；Runtime 是 operation/inbox owner，Task 事件由 Worker 自己持久化，跨服务重放可收敛，不依赖分布式事务。

### Checklist

- [x] 调用身份唯一为 tenant+owner_type+owner_id+source_tool_call_id；input_hash 只作冲突判定，同 call 换参数返回 TOOL_OPERATION_INPUT_MISMATCH。
- [x] Task-owned 操作不查找或伪造 Run/Conversation；ToolOperation.task_id 仍是实际提交产生的任务，owner_task_execution_id 单独存放。
- [x] 结果按 event_id 幂等落 inbox，保留每个 call 的 receipt 身份/序号；Task 与 Run 的 receipt kind 分流，禁止写 canonical Run 事件冒充 Task receipt。
- [x] 定义可重放的 Task 结果读取与消费确认边界，由 Worker 的消费事务写 task_event/checkpoint/水位；确认丢失可重投，不提前丢结果。
- [x] 取消意图先持久化，覆盖 cancel-before-submit、提交回执丢失、重复提交/结果、迟到结果；没有 Runtime→Worker 入站回调，wake-up hint 与 cancel hint 分开。
- [x] 回归原 RUN JOIN/DETACH/outbox/continuation，不让 Task 结果进入会话通知。

### Spec Delta

```json
[
  {
    "id": "DELTA-001",
    "op": "add",
    "target": "worker/harness-worker",
    "rule": "异步工具调用以租户、owner 与真实工具调用身份唯一，输入指纹用于同身份冲突判断，不能参与唯一身份以放行变参重试。提交与结果必须可持久重放；等待前检查结果就绪，按每个调用保存独立 receipt 与消费水位，结果乱序或重复不得丢失或重复消费。持久收件箱与数据库补扫保证恢复正确性，独立唤醒 hint 仅降低延迟；终态收到迟到结果只留审计，不得恢复执行。",
    "evidence": "scheduled-task-v2.backend.design.md §3.1.5、§3.2.4；S-17 和 TASK-007/TASK-020。"
  }
]
```

### Log

- [2026-11-15] created (draft)
- [2026-11-15T06:31:21.559Z] started tests=bbb33e6b2e6c
- [2026-11-15] 契约先定 owner 联合：`RunToolOperationOwner`/`TaskToolOperationOwner` + 判别联合 `ToolOperationOwner` 移入 `tool_runtime`（`tasks → tool_runtime` 的既有方向决定了它不能放 scheduling）；`TaskToolOperationOwner` 仍在 scheduling 再导出，`ToolResultRequest` 的 `source_run_id` 换为 `owner`，`CancelOperationRequest` 同口径。新增 API-30 读模型 `TaskToolOperationView/Result/List/CancelRequest/CancelResponse`（列表按 RULE-api-001 带 page/page_size/total）。
- [2026-11-15] API-30 落 `api/tool_operations.py` + `application/async_tools/task_operations.py|task_facts.py`：`POST /internal/tool-operations`（submit-or-find）、`GET`（按 owner 列、`ready` 过滤、分页）、`GET /{id}`、`POST /cancel`（按调用身份）。归属按 `task.task_execution` 只读复核（跨租户 404、`X-Actor-User-Id` 不符或缺失 403、执行已取消/终态 409 TASK_OPERATION_CANCELLED）；同身份并发用 `pg_advisory_xact_lock` 串行化，partial unique 兜底。
- [2026-11-15] 提交语义定案：Runtime 只持 operation 身份与 inbox，**不替 Task 提交子任务**——请求体本来就不带快照/skill 材料，Worker 自己创建并执行内层 Task（`tool_operation.task_id` 因此仍由真实提交决定，结果到达时回填）。`submission_json` 由 owner 决定形状：RUN 存 CreateTaskRequest，TASK 存调用（tool_name/arguments/input_hash）。
- [2026-11-15] cancel-before-submit 用 tombstone：operation 行先落（CANCELLED + `cancel_requested`，`input_hash`/`task_snapshot_hash` 用显式占位符，**不参与冲突比较**），提交晚到时补齐真实身份但状态保持 CANCELLED；同一身份再取消不重复投递控制命令。已终态（结果已到）的迟到取消不写标记、不改写终态事实。
- [2026-11-15] 控制面复用同一 outbox：`CANCEL_OPERATION` 由 Runtime 的 `ControlDispatcher` 投给 Worker `/internal/runtime-operations/{id}/cancel`，投递体带 `owner` 联合；dispatcher 对 `run_id IS NULL` 的行不再加载/lock Run，`SUBMIT` 命令对 TASK 行直接视为不变量违规。Worker `application/runtime_operations.py` 的 `lock_operation` 按 owner 写 `source_run_id`/`task_execution_id`（XOR），TASK 行只落墓碑、不查 task。
- [2026-11-15] 结果协议：`/internal/tool-results` 仍是一个入口，按 operation 行的 owner 分流。TASK 回执**由 Runtime 解析回执 id**：以 `(tenant, EXECUTION, task_id, task_event_seq)` 定位唯一的 `task.task_event`（唯一键同源），并校验子 Task 的 `snapshot_hash` 与回执声明一致；inbox 写 `receipt_event_type=TASK_EVENT` + 该事件 id + `task_execution_id`，`canonical_event_id` 为空——绝不写 canonical Run 事件冒充 Task receipt，也不建 continuation/会话通知。
- [2026-11-15] 读取/消费边界：Runtime 侧**没有**消费确认端点，`GET`/列表都是可重放只读（不删行、不标记 `materialized`）；消费水位（checkpoint/wait_payload/`task_event`）由 Worker 自己的事务写，确认丢失只会重投。迟到 = 取消/已终态（TASK 无 Run 可查），只记 `late`。
- [2026-11-15] hint 口径：本 TASK 不引入 Runtime→Worker 的入站回调，也不新增 hint 生产者（持久 inbox + PG 补扫才是正确性来源，hint 只降延迟）；Worker 既有的 `muad:task:wakeup`（pubsub）与 `muad:task:cancel:{id}`（key）保持两条独立通道，取消权威仍是被持久化的 operation 行。
- [2026-11-15] 顺带对齐（被声明测试逼出、非本 TASK 主体）：`worker/executor.py::task_env` 读 `wait_payload_json.external_ref`（TASK-002 已删 `external_ref_json`）；`application/terminal_tasks.py::_payload` 改传 `RunToolOperationOwner`；`tests/async_tool_continuations.py`/`tests/contracts/test_runtime_strict_json.py`/`tests/acceptance/security/test_internal_service_identity.py` 同步 owner 形状。
- [2026-11-15] 测试：`test_scheduled_v2_task_operations.py`（13，真实 uvicorn + 真实 PG：身份唯一/并发/归属/取消墓碑/分页/控制命令投递）、`test_scheduled_v2_task_inbox.py`（13，真实 PG：kind 分流/绑定校验/幂等/迟到/可重放/RUN 回归）；同批回归 `test_task_operation_identity.py`、`test_runtime_operation_races.py`（13）及 `test_tool_result_materialization.py`、`test_resume_transactions.py`、`test_tool_wait_state_machine.py`、`test_scheduled_v2_transactions.py`、`tests/contracts/test_scheduled_v2_contracts.py`。
- [2026-11-15] 明确留给后续 TASK 的接缝：Task-owned 终态结果的 outbox 投递（`write_terminal` 只在 root JOIN 且 `source_run_id` 存在时入队，TASK 行需要把 `task_event_seq` 交给 Runtime 解析）由 TASK-015/TASK-020 接；本 TASK 只交付协议与接收侧，不伪造 producer。

### Verification

- 尚未执行；此 TASK 的声明测试、业务场景均未宣称通过。

<a id="task-008"></a>
- 2026-11-15T06:46:41.930Z PASS commands=1/1 executed=1 deduped=0 elapsed=25153ms tests=bbb33e6b2e6c head=ccf6da6c7092 dirty=1
  - related PASS exit=0
## TASK-008: 新增 Task 产物写入与不可变版本 API

- **Status**: done
- **Depends**: TASK-001, TASK-002, TASK-003
- **Source**: scheduled-task-v2.backend.design.md §3.1.5、API-28、§3.4.6、§3.5.9、§4.4
- **Scenarios**: S-02, S-03, B-09, B-11
- **Spec-Refs**: auth/harness-auth, secret/harness-secret, persistence/harness-data, api/harness-api, test/harness-test
- **Paths**: apps/agent-runtime/src/muad_agent_runtime/api/artifacts.py, apps/agent-runtime/src/muad_agent_runtime/application/attachments/**, apps/agent-runtime/src/muad_agent_runtime/application/**/*artifact*, packages/artifact-store/src/**, tests/agent_runtime/test_scheduled_v2_task_artifacts.py
- **E2E**:
- **Skip-Tests**: false
- **Skip-Reason**:

### Tests

```json
[
  {
    "id": "related",
    "argv": [
      "uv",
      "run",
      "python",
      "-m",
      "pytest",
      "-q",
      "tests/agent_runtime/test_scheduled_v2_task_artifacts.py",
      "tests/agent_runtime/test_artifact_results.py",
      "tests/agent_runtime/test_output_artifacts.py"
    ],
    "timeout": 600,
    "parallel": false
  }
]
```

### Description

在现有仅 resolve/读取的 artifact HTTP 面上增加 Task-owned 写入、登记和追加版本能力，为长工具结果、最终产物和任务中心下载提供唯一存储来源。

### Checklist

- [x] 服务身份加 TASK owner 核属后写 runtime.artifact.task_id=Execution.id，run_id 为 NULL；存储键按 tenant/Execution 生成，不要求 Conversation。
- [x] 写入及追加版本按稳定调用标识幂等；每个版本有不可变 version/checksum/name/media_type/size，重放返回原版本，冲突显式失败。
- [x] 读指定版本必须校验 checksum；不得用最新版替代；缺失对象、写入失败或元数据落库失败有明确错误与清理策略。
- [x] 区分用户可下载结果与内部 transcript/审计留档；内部原文不能通过新增下载能力暴露。
- [x] 覆盖 XOR、无 Run 写入、重复版本请求、跨 tenant/actor/agent、hash 一致性与存储失败；保留 Run 产物回归。

### Spec Delta

```json
[]
```

### Log

- [2026-11-15] created (draft)
- [2026-11-15T06:18:35.034Z] started tests=d61cc21f9846
- [2026-11-15] 新增 `application/attachments/task_artifacts.py`：API-28 写入/追加不可变版本（multipart：契约 `TaskArtifactWriteRequest` + 字节），产物行 `task_id=task_execution.id`、`run_id=NULL`、无 Conversation，存储键 `tasks/{tenant}/{execution}/{artifact_id}/v{n}`；TASK owner 只读 `task.task_execution`（同一 PG，只读投影，先例 `channel_uploads`/`model_dispatch_store`）复核 tenant/actor/agent，请求体不能自报归属。
- [2026-11-15] 版本语义：同一 Execution 内 `filename` 是产物身份，`base_version` 是 CAS（0 新建、N 追加 N+1）；稳定调用标识 = (filename, base_version, checksum, media_type, size)，重放返回原版本、不重复落盘；内容不同/过期 base 一律 `ARTIFACT_VERSION_CONFLICT`。并发由 `pg_advisory_xact_lock(tenant, execution, filename)` 串行化（行还不存在时行锁兜不住）。
- [2026-11-15] 读面：`GET /internal/artifacts/{artifact_id}/versions/{version}?checksum=&user_id=&agent_id=`（TASK-024 的 Owner 侧）只解析指定不可变版本，按 `artifact.task_id → task_execution` 核属 tenant/actor/agent；版本/checksum 不匹配 409、字节缺失或长度不符 404，绝不返回最新版。可下载类型白名单只含 `TASK_OUTPUT`：`TOOL_RESULT`/`TRANSCRIPT` 等内部留档即使挂 `task_id` 也 404。
- [2026-11-15] 失败与清理：存储写失败 → 500 `COMMON_INTERNAL_ERROR`（不落行）；元数据提交失败 → 删除本次写的文件（历史版本字节不动）；同键已有同内容字节视为已发布（崩溃后重试复用），不同内容 → 409。
- [2026-11-15] 测试 `tests/agent_runtime/test_scheduled_v2_task_artifacts.py`（15 例，真实 PG + 真实共享存储）：XOR/无 Run、重放幂等、改名冲突、追加不可变、精确版本读取、跨 tenant/actor/agent、缺失/损坏对象、内部留档与 Run 产物不可下载、服务身份门控、checksum/size 一致性、空结果、坏元信息、存储失败、提交失败清理；Run 产物回归沿用 `test_artifact_results.py`/`test_output_artifacts.py`。
- [2026-11-15] 未做（归属后续 TASK）：WebChat/网关侧下载路由与流式字节由 TASK-024 实现；Worker 侧写入客户端与长结果入站接入由 TASK-019 实现；本次只交付 Runtime Owner 面（API-28 写 + 版本读取）。

### Verification

- 尚未执行；此 TASK 的声明测试、业务场景均未宣称通过。

<a id="task-009"></a>
- 2026-11-15T06:26:36.205Z PASS commands=1/1 executed=1 deduped=0 elapsed=12682ms tests=d61cc21f9846 head=a0a06fc600af dirty=1
  - related PASS exit=0
## TASK-009: 泛化 Kernel 的挂起、事件与恢复端口

- **Status**: done
- **Depends**: TASK-001
- **Source**: scheduled-task-v2.backend.design.md §3.1.2、§3.2.4、§3.5.6
- **Scenarios**: S-03, S-09, S-15, S-17, B-08, B-09
- **Spec-Refs**: snapshot/harness-snapshot, memory/harness-memory, mcp/harness-mcp, secret/harness-secret, worker/harness-worker, test/harness-test
- **Paths**: packages/agent-core/src/muad_agent_core/agent/**, packages/agent-core/src/muad_agent_core/tools/**, packages/agent-core/src/muad_agent_core/context/**, tests/agent_core/test_scheduled_v2_kernel_ports.py, tests/agent_core/test_runner.py, tests/architecture/test_scheduled_v2_kernel_boundaries.py
- **E2E**:
- **Skip-Tests**: false
- **Skip-Reason**:

### Tests

```json
[
  {
    "id": "related",
    "argv": [
      "uv",
      "run",
      "python",
      "-m",
      "pytest",
      "-q",
      "tests/agent_core/test_scheduled_v2_kernel_ports.py",
      "tests/agent_core/test_runner.py",
      "tests/architecture/test_scheduled_v2_kernel_boundaries.py"
    ],
    "timeout": 600,
    "parallel": false
  }
]
```

### Description

复用已存在的 AgentRunner/RunnerCheckpoint，去掉 ToolSuspensionPort 对 Memory 请求形状的直接依赖，建立 Runtime/Worker 都能实现的中立整回合及 pending tool call 协议；不重写 Agent 循环。

### Checklist

- [x] 明确 ExecutionSpec、事件、CheckpointStore、模型/容量/凭据/工具/产物/取消端口；Kernel 不 import 任一 app、ORM 或渠道适配器。
- [x] Human 仅在完整模型回合边界挂起；ASYNC 工具级挂起保存 pending_tool_calls[]、call_id 与独立结果状态，支持同回合多个 operation。
- [x] Memory 确认适配中立协议，交互式 Run 原有记忆确认效果不丢；不能把所有挂起都解释为 Memory。
- [x] 为两份适配器定义同一组可参数化的行为契约：挂起/恢复、工具授权、预算、取消、迟到结果与事件顺序。
- [x] 工具重试保留 READ_ONLY/IDEMPOTENT/NON_RETRYABLE；有副作用且结果未核实不能盲目重跑；保留 verifier、并行/串行工具与输出契约。
- [x] 压缩每层失败均退回原历史/inline 结果，整轮 artifact 写入失败整体回滚，并留下 warning 与失败指标；测试不能以吞掉异常代替可见降级。

### Spec Delta

```json
[
  {
    "id": "DELTA-001",
    "op": "add",
    "target": "snapshot/harness-snapshot",
    "rule": "共享 Agent 执行内核仅依赖中立执行契约和端口，既有 Agent 循环由不同宿主适配，不得复制简化循环或按宿主类型分支。持久检查点必须覆盖完整模型回合与异步工具挂起所需的调用、结果消费和预算状态；恢复同一执行不能重新解析当前定义。上下文压缩失败必须保留原历史，批量工具结果外置失败整轮回滚为内联结果，并记录可观测的降级，不能使主执行失败。",
    "evidence": "scheduled-task-v2.backend.design.md §3.1.2、§3.2.4、§3.5.6；项目 Compaction 核心原则及 TASK-009/TASK-018/TASK-019 共享契约。"
  }
]
```

### Log

- [2026-11-15] created (draft)
- [2026-11-15T05:52:38.640Z] started tests=ab2e2402d7e8
- [2026-11-15] 泛化挂起/恢复协议：`agent/suspension.py` 只保留中立 `SuspensionRequest`/`SuspensionResume`/`resume_checkpoint`/`ToolSuspensionPort`（问题、输入 schema、原因、恢复载荷）；Memory 形状移出 Kernel——`MemorySuspendRequest` 成为 Runtime 适配器里的子类（`application/memory_suspension.py`），Kernel 只原样透传 `reason`。
- [2026-11-15] checkpoint 覆盖两类挂起：`RunnerCheckpoint.pending_tool_calls`（`OperationWatermark` 按 call_id 聚合、各带独立水位）由 Kernel 写进 `port.suspend(progress)`；新增 `RunnerCheckpoint.as_state()` 给出 JSON-native 可持久形态，Runtime 的 `_write_checkpoint` 改用它（`asdict` 不递归序列化 contracts 模型，写 JSON 列会直接失败）。
- [2026-11-15] 事件/检查点端口去宿主名：`RuntimeEventPort`/`RuntimeEventBatch` → `ExecutionEventPort`/`ExecutionEventBatch`（只有类型名，实现名与调用点不变）。
- [2026-11-15] 路由修正：`_route_after_tools` 与 `_finalize` 现在把 `WAITING_TOOL` 也当挂起收口——此前只认 `WAITING_INPUT`，工具级挂起会被当成"这一轮没事发生"继续跑下一个模型回合。
- [2026-11-15] 降级兜底落在 Kernel：压缩失败退回原历史、整轮外置失败保持内联结果，两者都写 warning（`context_compaction_failed` / `tool_round_results_failed`），不再只靠每个适配器自己记得吞异常；Runtime 侧的分层指标与整批回滚保持不变。
- [2026-11-15] 工具重试安全性仍是 Owner 侧 `ToolDefinition.retry_safety`（READ_ONLY/IDEMPOTENT/NON_RETRYABLE，默认 NON_RETRYABLE），本次不改口径；Kernel 侧新增断言：带 pending operation 的恢复不重跑工具（有副作用且结果未核实不得盲目重放）。
- [2026-11-15] 测试：`tests/agent_core/test_scheduled_v2_kernel_ports.py` 用同一组端口的两份内存适配器（Runtime 形状 HUMAN_INPUT / Worker 形状 TOOL_OPERATION）跑参数化契约（挂起、工具授权、预算、取消、迟到结果、事件顺序、恢复）；`tests/architecture/test_scheduled_v2_kernel_boundaries.py` 静态钉住 Kernel 不 import 宿主/ORM、挂起端口不含 Memory 形状、无宿主分支与 stub。
- [2026-11-15] 回归：`tests/agent_core` + `tests/architecture` 578 passed；相邻 `tests/agent_runtime` 的记忆确认/等待状态机/产物预算 106 passed。
- [2026-11-15] 未做（下游 TASK 承接）：容量租约与凭据解析仍由适配器在构造 provider 时完成，Kernel 不新增 `CapacityLeasePort`/`CredentialResolver` 抽象；Worker 侧的实际装配与 Runtime 生产路径切换由 TASK-018/TASK-019 落地。

### Verification

- 尚未执行；此 TASK 的声明测试、业务场景均未宣称通过。

<a id="task-010"></a>
- 2026-11-15T06:13:45.778Z PASS commands=1/1 executed=1 deduped=0 elapsed=3666ms tests=ab2e2402d7e8 head=6665e3589171 dirty=1
  - related PASS exit=0
## TASK-010: 实现每次 Admission 与可重建 Snapshot v4

- **Status**: done
- **Depends**: TASK-001, TASK-005, TASK-006, TASK-007, TASK-008
- **Source**: scheduled-task-v2.backend.design.md §2.3.3、§2.3.4、§3.5.5、§3.5.7
- **Scenarios**: S-02, S-03, S-04, S-15, S-16, S-17, E-02, E-10, E-13, B-08
- **Spec-Refs**: snapshot/harness-snapshot, auth/harness-auth, memory/harness-memory, model/harness-model, mcp/harness-mcp, artifact/harness-skill, secret/harness-secret, test/harness-test
- **Paths**: packages/contracts/src/muad_contracts/snapshot.py, packages/contracts/src/muad_contracts/resolve.py, apps/console-platform/backend/src/muad_console_platform/application/resolve_service.py, apps/console-platform/backend/src/muad_console_platform/api/**, apps/agent-worker/src/muad_agent_worker/application/**, apps/agent-worker/src/muad_agent_worker/scheduler/client.py, tests/console_internal/test_scheduled_v2_admission_materials.py, tests/agent_worker/test_scheduled_v2_snapshot.py
- **E2E**:
- **Skip-Tests**: false
- **Skip-Reason**:

### Tests

```json
[
  {
    "id": "related",
    "argv": [
      "uv",
      "run",
      "python",
      "-m",
      "pytest",
      "-q",
      "tests/console_internal/test_scheduled_v2_admission_materials.py",
      "tests/agent_worker/test_scheduled_v2_snapshot.py",
      "tests/agent_runtime/test_snapshot_freeze.py"
    ],
    "timeout": 600,
    "parallel": false
  }
]
```

### Description

将当前依赖解析、授权和 Snapshot 冻结作为一次有界 Admission；每次 Fire 重新 resolve latest，Execution 独立保留可恢复的非密钥材料，不把整库 catalog 或 Skill 代码字节塞进快照。

### Checklist

- [x] 按模式验证 Agent/Skill/Model/Tool/MCP/Grant、route 能力、输入变量、budget 与 ASYNC 协议；REMINDER 不依赖 Skill/Model。
- [x] 冻结完整 Agent instructions/runtime_config、模型定义/池成员/summary model、Prompt 模板、Policy、allowlist 工具完整 schema 与涉及的所有 Skill artifact 元数据/checksum/frontmatter。
- [x] MCP 保留 endpoint/非密 headers/secret_header 与完整 definitions；凭据只留引用，Secret 从内容与 hash 同时剥离。
- [x] 任务记忆读取一次，冻结实际 READ_ONLY 注入块及 taxonomy/revision/hash；失败为空块+memory_degraded+warning/事件，不阻断；后续 Human/ASYNC resume 不刷新记忆。
- [x] 严格 canonical hash 与实际序列化字节数；超过设置上限明确 SNAPSHOT_TOO_LARGE，不截断；Snapshot 与 Execution 同生命周期保留。
- [x] 错误分类返回永久/临时与用户修复动作；静态 Preflight 不运行业务、不产生扫描或写副作用；网络 IO 在锁外，恢复只读原快照。

### Spec Delta

```json
[
  {
    "id": "DELTA-001",
    "op": "update",
    "target": "snapshot/harness-snapshot",
    "ref": "RULE-snapshot-001",
    "before": "每个新 Run/Task 在执行前冻结 RuntimeSnapshot/execution snapshot（Agent/Model/Skill/MCP 版本、Prompt 模板版本、catalog revision/hash；**预算只属 execution snapshot 的 `budget` 键**——Run 侧的 `RuntimeSnapshot` 无 budget 列，等价载体是 `policy_json`，默认含 `max_model_retries`，预算与压缩策略等执行期策略一并冻结在此键）；配置或授权变更只影响后续新 Run/Task；终态写入必须 CAS。",
    "rule": "每个新 Run/Task 在执行前冻结其执行契约要求的完整非密钥材料，包括 Agent/Model、模型池与摘要模型、Prompt 模板、执行策略、工具定义及依赖内 Skill/MCP 材料；Task 快照按执行模式约束依赖集合，不能用固定单 Skill 假设覆盖所有模式，也不能以 hash 替代恢复所需内容。任务预算归 execution snapshot，Run 的等价策略载体保持其自身契约。定时任务冻结实际只读记忆块及版本，等待与恢复使用原块；读取失败显式记录降级。快照大小按设置所有者约束，超限拒绝而非截断；快照、检查点随执行保留，配置变化只影响后续新执行，非终态与终态写入均受状态及执行权 CAS 保护。",
    "evidence": "scheduled-task-v2.backend.design.md §3.5.7；S-04/S-16/S-17，TASK-010 Snapshot 冻结与恢复回归。"
  },
  {
    "id": "DELTA-002",
    "op": "update",
    "target": "memory/harness-memory",
    "ref": "RULE-memory-003",
    "before": "候选查询读取用户完整有界集合，禁止 latest-N 截断；非空 SEARCH 先匹配，零覆盖返回空，空 query 为 LIST，偏好注入不依赖 query。用户记忆 v2 在覆盖率/BM25 正分之上执行 scope 三态与 NOTE 实体门控：带 entity 标签的候选必须有正向匹配，且候选全部实体标签都出现在 query 中；无匹配时带 entity 标签的候选不注入，无标签 NOTE 仅按 topic。冷/热读取必须以 PG head revision 为准，逐次按当前语句时间过滤 TTL；经验/TASK_STATE 只冻结准入 key，行状态/版本/TTL 随快照刷新；读取失败清缓存并如实返回 degraded。每个模型请求前刷新独立 MemoryComponent，完整 SYSTEM 块及 recall JSON 严守最终 UTF-8 字节与条数预算；0 关闭注入，长条跳过，无首条越限保底，压缩不能吞掉记忆组件。",
    "rule": "候选查询读取用户完整有界集合，禁止 latest-N 截断；非空 SEARCH 先匹配，零覆盖返回空，空 query 为 LIST，偏好注入不依赖 query。用户记忆 v2 在覆盖率/BM25 正分之上执行 scope 三态与 NOTE 实体门控：带 entity 标签的候选必须有正向匹配，且候选全部实体标签都出现在 query 中；无匹配时带 entity 标签的候选不注入，无标签 NOTE 仅按 topic。交互式 Run 的冷/热读取必须以 PG head revision 为准，逐次按当前语句时间过滤 TTL；经验/TASK_STATE 只冻结准入 key，行状态/版本/TTL 随快照刷新；读取失败清缓存并如实返回 degraded。交互式 Run 在每个模型请求前刷新独立 MemoryComponent；完整 SYSTEM 块及 recall JSON 严守最终 UTF-8 字节与条数预算；0 关闭注入，长条跳过，无首条越限保底，压缩不能吞掉记忆组件。定时 Task 的准入读取沿用上述选择、TTL、预算与失败降级约束，冻结实际 READ_ONLY 注入块及其版本；等待和恢复始终使用原块，不重新读取当前记忆或刷新块内条目的状态、版本与 TTL，也不写长期记忆。",
    "evidence": "scheduled-task-v2.backend.design.md §3.5.7 的 Task READ_ONLY 冻结语义与现有交互式 Run 刷新规则需要显式划分；S-16，TASK-010/TASK-019。"
  }
]
```

### Log

- [2026-11-15] 收尾补正：本任务把 `PUSH_CAPABLE_CHANNELS = frozenset({"WECOM"})` 写进了核心域，踩中 `tests/architecture/test_channel_neutrality.py` 的通道中立守卫（渠道名只允许留在 im-gateway 适配器层）。已改为消费共享契约 `muad_contracts.enums.OUTBOUND_CHANNELS`，守卫 3 passed，准入/快照/Draft 相关 55 passed。
- [2026-11-15] created (draft)
- [2026-11-15] 新增 `apps/agent-worker/.../application/schedule_admission.py`：每次 Fire 的 Admission 入口（按模式校验 → 组装 Snapshot v4 → 失败台账），纯组装函数无 IO，网络取数只在 `admit()` 且不持任何锁。
- [2026-11-15] Console 新增 `POST /internal/runtime/schedule-admission-materials` + `ScheduleResolveService`（与交互式 `ResolveService` 分离）：只读 SELECT（用例断言零 INSERT/UPDATE/DELETE），按模式解析 Skill/MCP/Model/Grant/记忆候选/设置快照。
- [2026-11-15] 快照口径：v4 由 `packages/contracts/.../snapshot.py::build_schedule_snapshot` 唯一产出，v3（`build_task_snapshot`）保持原样不扩展；`snapshot_bytes` 是「含自身」的规范字节数（不动点迭代），`snapshot_hash` 对落库文本取 sha256；`load_schedule_snapshot` 只认 `schema_version=4`，重建不读任何当前配置。
- [2026-11-15] 记忆：AGENTIC 在准入读取一次（v2 事实行，TTL 逐次过滤，硬上界 `MEMORY_MAX_RECORDS`），复用 `muad_agent_core.memory.select_memory` 的 LIST 口径（无 query ⇒ 只走偏好车道）+ `render_memory` 渲染；冻结 entries/rendered_text/revision/payload_hash。读取失败 `degraded=True` + 空块 + warning 日志，不阻断任务。
- [2026-11-15] 错误码口径：HTTP 沿用既有码（`SCHEDULE_DEPENDENCY_MISSING`/`SCHEDULE_GRANT_DENIED`/`AGENT_*`/`MODEL_*`/`CREDENTIAL_MISSING`/`ASYNC_OPERATION_UNAVAILABLE`/`SCHEDULE_DELIVERY_UNSUPPORTED`），精确原因与修复动作走 `data.admission_code`/`data.repair`，落在 `schedule_fire.admission_error_code`；未登记的新码按临时处理（一次未知错误不判死用户任务）。
- [2026-11-15] Prompt 模板冻结为 `{"version": "v1", "source": "agent.instructions"}`：模板文本本体就是已冻结的 `agent.instructions`，不复制第二份；模型 `policy_hash` 对冻结 policy 取哈希。
- [2026-11-15] 投递能力：`delivery_capability_error` 按渠道判 PUSH（IM 可 push，WebChat/MSSWWEB 只 pull）；渠道值由 Fire 步骤解析 `delivery_route` 后传入，未解析时不做假设、不拦任务中心 pull 路径。
- [2026-11-15] 承接边界：Fire/Execution 的 CAS 与同事务插入归 TASK-014（本任务交付 `execution_snapshot_columns` 与 `record_admission_failure`）；恢复侧消费冻结块归 TASK-019；`tests/agent_worker/conftest.py` 补了 Fire↔Execution 循环 FK 的清扫（回填过 `execution_id` 的 Fire 先摘引用，否则租户清扫撞 FK）。
- [2026-11-15T06:49:28.408Z] started tests=44c62f1c9918

### Verification

- 尚未执行；此 TASK 的声明测试、业务场景均未宣称通过。

<a id="task-011"></a>
- 2026-11-15T07:01:06.355Z PASS commands=1/1 executed=1 deduped=0 elapsed=15341ms tests=44c62f1c9918 head=af5da4d1727e dirty=1
  - related PASS exit=0
## TASK-011: 创建可预览且无副作用的 Schedule Draft

- **Status**: done
- **Depends**: TASK-003, TASK-004, TASK-005, TASK-010
- **Source**: scheduled-task-v2.backend.design.md §2.3.3、§3.3.4、API-01、§3.5.4
- **Scenarios**: S-01, S-02, S-03, E-01, E-04, E-14, B-04, B-07
- **Spec-Refs**: api/harness-api, auth/harness-auth, time/harness-time, snapshot/harness-snapshot, secret/harness-secret, test/harness-test
- **Paths**: apps/agent-worker/src/muad_agent_worker/api/**, apps/agent-worker/src/muad_agent_worker/application/**, tests/agent_worker/test_scheduled_v2_drafts.py
- **E2E**:
- **Skip-Tests**: false
- **Skip-Reason**:

### Tests

```json
[
  {
    "id": "related",
    "argv": [
      "uv",
      "run",
      "python",
      "-m",
      "pytest",
      "-q",
      "tests/agent_worker/test_scheduled_v2_drafts.py"
    ],
    "timeout": 600,
    "parallel": false
  }
]
```

### Description

交付 API-01 与 ScheduleDraftService：创建/更新意图先规范化、Preflight、持久化 PENDING Draft，返回做什么、何时、时区、结果在哪看和依赖预算，尚不产生 ACTIVE Schedule。

### Checklist

- [x] 身份来自可信上下文；UPDATE Draft 校验目标 owner/agent，绑定 base_schedule_id/base_config_version；body 不接受 owner。
- [x] 模式、Context、时间、delivery intent、allowlist、budget 一并进入 canonical draft_hash；Secret 扫描命中明确拒绝或按契约脱敏。
- [x] 创建 Draft 使用独立 endpoint 与幂等键；同键同指纹重放，变参冲突；缺时区/变量/依赖可解释，原 Draft 不静默变更。
- [x] 复用 Admission 静态校验，Skill P0 只校验模板/变量，不虚构 input_schema 或提前执行业务。
- [x] 预览只暴露必要依赖摘要、修复动作与 UPDATE 差异；覆盖取消、到期、同名多匹配和跨用户/租户拒绝。

### Spec Delta

```json
[]
```

### Log

- [2026-11-15] created (draft)
- [2026-11-15T07:01:51.876Z] started tests=89e5df6964ee
- [2026-11-15] 新增 `application/schedule_drafts.py`：`parse_draft_request`（缺时区→`SCHEDULE_TIMEZONE_REQUIRED`、缺必填→`SCHEDULE_PARAMETER_INCOMPLETE` 并列出字段、形状非法→`SCHEDULE_DRAFT_INVALID`）+ `ScheduleDraftPreflight`（纯函数校验 + 一次只读材料解析）+ `ScheduleDraftService`（落一条 PENDING Draft 后返回预览，不 commit）。
- [2026-11-15] 新增 `api/schedule_drafts.py`：`POST /internal/schedule-drafts`（API-01），挂 `require_internal_service`；身份全部来自可信上下文头（`X-Tenant-Id`/`X-Actor-User-Id`/`X-Agent-Id`/`X-Entry-Channel`），body 不接受 owner/agent；`api/deps.py` 增对应依赖与材料客户端缝（`app.state.schedule_materials_client` 可注入，否则用 lifespan 的连接池构造 `ConsoleResolveClient`；两者都缺 → `SCHEDULE_ADMISSION_FAILED`，不假装无依赖）。`main.py` 增两行挂载（`include_router`）。
- [2026-11-15] 幂等：独立 endpoint `schedule-drafts` + `Idempotency-Key`，走 TASK-003 台账 `admit()`（`pg_advisory_xact_lock` 串行化同键）→ 同键同指纹直接重放首次响应（**先于任何状态/过期检查**，Draft 已 CANCELLED/EXPIRED 也不复活不改写）→ 异指纹 `IDEMPOTENCY_MISMATCH`。指纹 = 规范化 JSON（endpoint/tenant/actor/target/整体请求体 + 入口 surface），入口通道影响投递能力，故计入指纹。
- [2026-11-15] `draft_hash` = `payload_json` 的 canonical JSON sha256（`sha256:`+64hex，与列 CHECK 同格式），覆盖 mode/recurrence/context/execution_spec（含 allowlist/budget）/delivery_intent；落库载荷与 hash 是同一份事实，测试用 `payload_json` 重算比对。`expires_at` 取 `schedule.confirmation_ttl_sec`（平台设置边界快照），不写死时长。
- [2026-11-15] Preflight 口径：复用 Admission 的 `classify_failure`/`allowlist_key`/`REPAIR_ACTIONS` 与 `validate_input_template`；材料只经 Console 只读端点取一次。错误码落到 §3.4.7：授权类（`GRANT_REVOKED`/`TOOL_REVOKED`）→`SCHEDULE_GRANT_DENIED`，其余永久失败→`SCHEDULE_DEPENDENCY_MISSING`，瞬时失败→`SCHEDULE_ADMISSION_FAILED`；精确原因与修复动作留在 `data.admission_code`/`data.repair`。配额：CREATE 才占名额（`ensure_schedule_capacity`），两档都校验最小间隔。Skill 只校验模板语法/变量（未知变量 → `SCHEDULE_PARAMETER_INCOMPLETE` + `unknown_variables`），不虚构 input_schema、不执行业务。
- [2026-11-15] 投递意图由入口 surface 的静态能力决定（`OUTBOUND_CHANNELS`/`LOCAL_STREAM_CHANNELS`，词表归契约定义处，worker 不点名任何渠道）：body 与能力不一致即 `SCHEDULE_DELIVERY_UNSUPPORTED`（`DELIVERY_INTENT_MISMATCH`），未登记通道 `ENTRY_CHANNEL_UNSUPPORTED`，缺入口通道时 PUSH 无法验证（`ENTRY_CHANNEL_UNKNOWN`）。
- [2026-11-15] Secret 扫描：instruction/name/context/execution_spec 命中私钥/厂商 key/`key=value` 形状即 `SCHEDULE_DRAFT_INVALID`（`reason=SECRET_DETECTED` + 字段名与类别），只报类别不回显原文；不建 Draft。
- [2026-11-15] 无副作用以**行数**证明（`tests/agent_worker/test_scheduled_v2_drafts.py`）：CREATE/UPDATE Draft 之后 `task_schedule`/`task_schedule_revision`/`schedule_fire`/`task_execution`/`task_delivery`/`task_human_interaction`/`task_event` 一条不增，只有 `task_schedule_draft` 与 `task_submission` 各一行；UPDATE 不新增 Schedule/Revision。
- [2026-11-15] UPDATE 预览差异：与目标 Schedule 的当前 Revision 逐字段比 `name/mode/delivery_intent/recurrence/context/execution_spec`，`run_at` 按 UTC 归一后再比；`name` 缺省沿用原名。差异放在 `preview.diff`（契约 `SchedulePreview` 未含该键，确认卡片资产需要，TASK-017 消费时按代码事实扩展契约）。
- [2026-11-15] 承接边界：确认/生效（API-27、surface/ref 唯一、CAS、过期裁决）归 TASK-012，本任务不提供任何 create/update 生效入口；`SCHEDULE_AMBIGUOUS` 在结构化载荷下没有触发点（目标由 `base_schedule_id` 指定），自然语言多匹配澄清归 TASK-017 的入口解析层；旧 `/internal/schedules` 的 POST/PUT 未改动（其 `_payload` 仍引用 v2 已删除的列，属 TASK-013/TASK-023 的重写范围）。
- [2026-11-15] 已知环境红：`tests/architecture/test_channel_neutrality.py` 两条在 HEAD 即失败，唯一命中的是 TASK-010 的 `schedule_admission.PUSH_CAPABLE_CHANNELS`（渠道字样），本任务新增代码已按通道中立口径改写、不再命中；未在本次改动中修它。
- [2026-11-15T07:14:14.261Z] reopen: refactor: split create into persist helper (function length rule)

### Verification

- 尚未执行；此 TASK 的声明测试、业务场景均未宣称通过。

<a id="task-012"></a>
- 2026-11-15T07:13:42.617Z PASS commands=1/1 executed=1 deduped=0 elapsed=14421ms tests=89e5df6964ee head=259b67644891 dirty=1
  - related PASS exit=0
- 2026-11-15T07:14:29.316Z PASS commands=1/1 executed=1 deduped=0 elapsed=14927ms tests=89e5df6964ee head=a8195135e680 dirty=1
  - related PASS exit=0
## TASK-012: 原子确认 Draft 并创建或更新 Revision

- **Status**: done
- **Depends**: TASK-003, TASK-004, TASK-005, TASK-011
- **Source**: scheduled-task-v2.backend.design.md §3.2.8、§3.3.4、API-27、§3.5.1、§3.5.4
- **Scenarios**: S-02, S-04, S-14, E-04, E-05, E-14, B-12
- **Spec-Refs**: api/harness-api, auth/harness-auth, persistence/harness-data, worker/harness-worker, time/harness-time, test/harness-test
- **Paths**: apps/agent-worker/src/muad_agent_worker/api/**, apps/agent-worker/src/muad_agent_worker/application/**, tests/agent_worker/test_scheduled_v2_confirm.py, tests/agent_worker/test_scheduled_v2_revisions.py
- **E2E**:
- **Skip-Tests**: false
- **Skip-Reason**:

### Tests

```json
[
  {
    "id": "related",
    "argv": [
      "uv",
      "run",
      "python",
      "-m",
      "pytest",
      "-q",
      "tests/agent_worker/test_scheduled_v2_confirm.py",
      "tests/agent_worker/test_scheduled_v2_revisions.py"
    ],
    "timeout": 600,
    "parallel": false
  }
]
```

### Description

API-27 是唯一创建/更新生效入口：一次用户确认事务同时写确认事实、Schedule/不可变 Revision、next_fire_at 与首次响应，生效不依赖模型下一次工具调用。

### Checklist

- [x] 仅 Gateway 的可信用户确认调用可达；复核 owner、hash、PENDING、时效与 surface/ref 唯一，确认来源绑定实际用户动作。
- [x] 重放检查优先于 Draft 过期/状态检查；同 key 同指纹原样重放，即使 Draft 已使用；另 key 重复使用或跨 Draft confirmation ref 重放明确拒绝。
- [x] CREATE 原子建立 Schedule+Revision；UPDATE 只读取 Draft 的 base_config_version 做 CAS，不接受 body 覆盖；冲突回滚确认，Draft 保持 PENDING。
- [x] 只有用户配置变化增加 config_version；正常 Scheduler 进度不使 Draft 失效；Revision 只追加不覆盖。
- [x] 关键配置变更取消旧 Revision 且 started_at IS NULL 的 Execution 及已存在 Fire；已开始后 reclaim 回 QUEUED 的任务保留，不伪造尚未生成的 Fire。
- [x] 仅名称/标签等展示字段变动不取消执行；覆盖并发确认、并发更新、确认后落库失败、ONCE 终态不得修改；模型自确认无任何业务副作用。

### Spec Delta

```json
[
  {
    "id": "DELTA-001",
    "op": "add",
    "target": "api/harness-api",
    "rule": "自然语言调度的创建与关键更新必须先生成完整 Draft，再由真实用户经鉴权 surface 明确确认；模型工具上下文不能确认自身提案。确认事实绑定 owner、完整载荷 hash、目标与配置版本、有效期及唯一用户动作引用，确认与配置生效、首次幂等响应同事务提交。幂等重放先于已消费或过期 Draft 检查；更新以 Draft 冻结版本 CAS，冲突不得覆盖配置或消耗确认。",
    "evidence": "scheduled-task-v2.backend.design.md §3.2.8、§3.4.4、§3.5.4；S-14/E-04/E-05/E-14，TASK-012。"
  }
]
```

### Log

- [2026-11-15] 收尾补正（协调侧）：`future_fire_at` 只回指针、丢弃了 `Advance.gap_events/slots_consumed`，导致创建时若首个槽落在 DST gap 里，既不计数也无审计事件。现返回整份 `Advance`，CREATE/UPDATE 同事务写 `occurrence_count` 与 `DST_GAP_SKIPPED` 事件；补了引擎层的口径单测。
- [2026-11-15] created (draft)
- [2026-11-15T07:16:07.663Z] started tests=560caf8738d3
- [2026-11-15] API-27 确认事务落地：`application/schedule_confirm.py`（Draft 行锁 → owner/hash/PENDING/时效/确认引用 → 确认事实 → Schedule+不可变 Revision+next_fire_at → 首次响应快照）、`api/schedule_drafts.py` 的 `POST /internal/schedule-drafts/{id}/confirm`（`task_submission` 重放先于状态检查）、`api/deps.py` 的确认来源头（`X-Confirmation-Surface`/`X-Confirmation-Ref`，缺失即 `SCHEDULE_CONFIRMATION_REQUIRED`）。
- [2026-11-15] 测试：`tests/agent_worker/test_scheduled_v2_confirm.py`（16）+ `tests/agent_worker/test_scheduled_v2_revisions.py`（10），真实 PG；原子性以注入失败后按真实行数断言；并发确认/并发更新用真实并发请求。
- [2026-11-15] `ruff check` 与 strict `mypy` 干净；`tests/architecture/test_channel_neutrality.py` 绿。
- [2026-11-15] 边界：Draft 不携带外部投递路由，PUSH 确认显式拒绝（`SCHEDULE_DELIVERY_UNSUPPORTED` / `DELIVERY_ROUTE_UNAVAILABLE`），路由接线归 TASK-022；名额配额仍在 Draft 创建边界校验，确认事务未重复校验。

### Verification

- 尚未执行；此 TASK 的声明测试、业务场景均未宣称通过。

<a id="task-013"></a>
- 2026-11-15T07:23:16.276Z PASS commands=1/1 executed=1 deduped=0 elapsed=14085ms tests=560caf8738d3 head=621ad13bf198 dirty=1
  - related PASS exit=0
## TASK-013: 实现暂停、恢复及两档取消的完整状态矩阵

- **Status**: done
- **Depends**: TASK-003, TASK-004, TASK-007, TASK-010, TASK-012
- **Source**: scheduled-task-v2.backend.design.md §3.2.5、§3.2.10、API-04/05/06/11、§3.4.5
- **Scenarios**: S-04, S-05, S-06, E-05, E-06, E-13, E-15, B-12
- **Spec-Refs**: worker/harness-worker, api/harness-api, persistence/harness-data, auth/harness-auth, test/harness-test
- **Paths**: apps/agent-worker/src/muad_agent_worker/application/**, apps/agent-worker/src/muad_agent_worker/api/**, tests/agent_worker/test_scheduled_v2_lifecycle.py, tests/agent_worker/test_task_cancel.py
- **E2E**:
- **Skip-Tests**: false
- **Skip-Reason**:

### Tests

```json
[
  {
    "id": "related",
    "argv": [
      "uv",
      "run",
      "python",
      "-m",
      "pytest",
      "-q",
      "tests/agent_worker/test_scheduled_v2_lifecycle.py",
      "tests/agent_worker/test_task_cancel.py",
      "tests/agent_worker/test_runtime_operation_races.py"
    ],
    "timeout": 600,
    "parallel": false
  }
]
```

### Description

所有入口共用 ScheduleService/ExecutionService，按锁序在同一事务内判断状态并落动作。取消不是删除历史，未来调度与已开始执行分别处理。

### Checklist

- [x] Pause 停未来且取消 QUEUED/started_at 为空的执行；已开始和 WAITING_* 保留；Resume 重新 Admission 成功才 ACTIVE，从 now 重算且不补发。
- [x] FUTURE_ONLY 保留 RUNNING、等待外部/人工及 OPEN interaction，已开始后回 QUEUED 的续接也保留；ALL_ACTIVE 才关闭 Human、协作停 RUNNING 并取消外部 operation。
- [x] 外部取消请求/失败持久化，网络在锁外，取消远端失败不撤销停未来；保留待核实或失败证据，不能假称副作用已回滚。
- [x] ENDED/CANCELLED 不得恢复；ONCE 准入失败只允许重新创建；不保留 cancel_running 或旧 pause/resume 双路由。
- [x] 覆盖 pause/claim、cancel/claim、update/fire、answer/cancel/deadline 的双侧 CAS；Schedule/Fire/Execution/Human/事件修改一起回滚。
- [x] 明确 Fire 保留创建事实及发生取消时的状态，不把 Execution/Delivery 成败反向当作 Schedule 状态；所有命令幂等回放。

### Spec Delta

```json
[]
```

### Log

- [2026-11-15] 收尾补正（协调侧）：`_resume_end_reason` 同样只取指针，恢复时被跳过的 gap 槽既不计 `occurrence_count` 也不写事件。现改为 async 并消费 `Advance.gap_events`，与 Scheduler 同一口径（仍不补暂停期：指针从 now 起算）。
- [2026-11-15] created (draft)
- [2026-11-15T07:24:03.942Z] started tests=313c2e016d33
- [2026-11-15] `application/schedule_lifecycle.py`：`ScheduleService.pause/resume/cancel/soft_delete` 与 `ExecutionService.cancel` 共用同一状态矩阵与锁序（Schedule 行锁 → Fire → root/parent/child → 事件）。`started_at IS NULL` 是「从未开始」的唯一判据（CAS，claim 赢了按新状态处理）；Pause/FUTURE_ONLY 只取消这类执行，RUNNING/WAITING_*/OPEN interaction 保留；ALL_ACTIVE 才终止 WAITING_*、协作标记 RUNNING、原子关闭 interaction。
- [2026-11-15] Resume：锁外 Admission（材料源或注入端口）→ 锁内复核 config_version/current_revision/status → ACTIVE 并用 `initial_fire_at(now)` 重算，不补发、不伪造 occurrence_count；ONCE 永久准入失败 → `ENDED(ADMISSION_FAILED)`（200 + `admission_error`，此后不可 resume），CRON 失败保持 PAUSED 报 `SCHEDULE_ADMISSION_FAILED`；ONCE 错过槽 → `ENDED(MISSED)`；until/max_occurrences 到点 → ENDED(COMPLETED/EXHAUSTED)；NEEDS_ATTENTION 恢复成功即清 attention。
- [2026-11-15] Fire 保留创建事实：取消只把 `status` 改为 CANCELLED 并写 FIRE 事件；Execution/事件/历史从不删除；soft delete 只打标记（对已删幂等）。Schedule 状态只由本域动作与准入结论决定，不被 Execution/Delivery 成败反向改写。
- [2026-11-15] 远端取消：`application/remote_cancellation.py` 在**提交之后**调用 Runtime API-30，事务内先写 ASYNC `CANCELLED{state:REQUESTED}`；失败只追加 `FAILED{error_code}` 证据，不回滚已停的未来调度；未装配端口时保持 REQUESTED（待核实）。`api/deps.get_tool_operation_canceller` 复用 `app.state.http_pools.egress`，测试可注入。
- [2026-11-15] `api/schedules.py` 退役 `POST`/`PUT`（API-02/03 由 Draft+确认取代），保读面为 v2 形状（列表/详情/Fire 历史），新增 `POST /{id}/pause|resume|cancel`（`Idempotency-Key`、endpoint `schedule-actions`、同键同指纹重放优先）；`api/executions.py` 新增 API-11 `POST /internal/executions/{id}/cancel`（endpoint `execution-cancel`，不改 Schedule 状态），main 注册；`api/admin_schedules.py` 方法/路径不变但改为复用同一 service（软删不删历史）。
- [2026-11-15] 跨任务修复（只对齐 TASK-003 的事件口径，不改语义）：`worker/claimer.py`、`worker/service.py` 的 `append_event(task_id=...)` → `(entity_type, entity_id)`，`TaskEventType.WAITING/RETRY` → `WAITING_EXTERNAL/QUEUED`。不修则声明的 `test_task_cancel.py` 在 claim/协作取消路径上全挂 TypeError。
- [2026-11-15] `test_task_cancel.py` 按新词汇对齐：`WAITING`→`WAITING_EXTERNAL`、事件查询改用 `(entity_type,entity_id)`；RUNNING 协作取消不再期待 `CANCEL_REQUESTED` 事件（§3.5.10 无此项，实体行 `cancel_requested` 才是权威），「API 回 RUNNING + 标记为真」的断言保留。
- [2026-11-15] 新增 `test_scheduled_v2_lifecycle.py`（35 用例）：pause/claim、cancel/claim、Scheduler 写 Fire/Execution 与 cancel 的 Schedule 行锁串行（取消不会漏掉并发提交的 occurrence）、update/pause 不复活 PAUSED、answer/cancel 与 deadline/cancel 双侧 CAS（已关闭/已超时的 interaction 回答与取消均为 0 行、不写事件）、ALL_ACTIVE 远端取消 REQUESTED→CONFIRMED/FAILED、事件写失败整体回滚（不留半个取消）、终态不可恢复、幂等重放优先于状态。
- [2026-11-15] 边界：deadline sweep 与 Human answer/timeout 的写侧仍由 TASK-015/021 交付，本任务只保证取消侧的 CAS 条件与它们互斥；`test_admin_schedules.py` 仍红属 TASK-029 的 Console 适配范围（其用例仍在造已退役的 create 请求与旧枚举）。

### Verification

- 尚未执行；此 TASK 的声明测试、业务场景均未宣称通过。

<a id="task-014"></a>
- 2026-11-15T07:47:52.261Z PASS commands=1/1 executed=1 deduped=0 elapsed=32649ms tests=313c2e016d33 head=4a6801d6c474 dirty=1
  - related PASS exit=0
## TASK-014: 生成 Fire 并原子提交 Execution 与调度进度

- **Status**: done
- **Depends**: TASK-003, TASK-004, TASK-005, TASK-010, TASK-013
- **Source**: scheduled-task-v2.backend.design.md §3.2.9、§3.3.5、§3.5.2、§3.5.3、§3.5.11
- **Scenarios**: S-04, S-07, S-08, E-02, E-03, E-06, E-10, E-13, B-01, B-02, B-03, B-04
- **Spec-Refs**: worker/harness-worker, time/harness-time, snapshot/harness-snapshot, persistence/harness-data, artifact/harness-skill, test/harness-test
- **Paths**: apps/agent-worker/src/muad_agent_worker/scheduler/**, apps/agent-worker/src/muad_agent_worker/application/**, tests/agent_worker/test_scheduled_v2_fire.py, tests/agent_worker/test_scheduler.py, tests/agent_worker/test_scheduler_misfire.py, tests/agent_worker/test_schedule_trigger.py
- **E2E**:
- **Skip-Tests**: false
- **Skip-Reason**:

### Tests

```json
[
  {
    "id": "related",
    "argv": [
      "uv",
      "run",
      "python",
      "-m",
      "pytest",
      "-q",
      "tests/agent_worker/test_scheduled_v2_fire.py",
      "tests/agent_worker/test_scheduler.py",
      "tests/agent_worker/test_scheduler_misfire.py",
      "tests/agent_worker/test_schedule_trigger.py"
    ],
    "timeout": 600,
    "parallel": false
  }
]
```

### Description

Scheduler 短事务选 due，锁外 Admission，随后锁 Schedule 做 status/revision/next_fire_at CAS；同事务插入唯一 Fire、冻结 Execution 和推进进度。Fire 只有台账职责，没有第二套 lease/claim。

### Checklist

- [x] occurrence 唯一键不含 revision，Execution key 用规范 UTC；Fire 冲突不重复执行业务，任何阶段故障都不会留下半个 Fire/Execution/进度。
- [x] SKIP_IF_RUNNING 的阻断集是全部非终态根执行，包含 QUEUED；Child 不阻断；重叠只记 SKIPPED，不创建 Execution。
- [x] 永久错误 CRON→NEEDS_ATTENTION，ONCE→ENDED(ADMISSION_FAILED)；临时错误按配置分类与计数，ONCE 重试只在本次锁外 Admission 内，Fire 落库后不补建 Execution。
- [x] misfire 一条 Fire 聚合区间，missed_count 仅额外次数；DST gap/second-fold 只写 SCHEDULE 事件，计数按本地槽，结束条件在同事务检查。
- [x] 进度不增加 config_version；所有 stale Admission（pause/update/cancel 赢）均丢弃；不下载 Skill 或调用模型，网络不持行锁。
- [x] 按 due 时间与租户前进游标有界扫描，单租户失败不阻塞后续租户；真实双 Scheduler/事务故障验证唯一性、原子性和公平性。

### Spec Delta

```json
[]
```

### Log

- [2026-11-15] created (draft)
- [2026-11-15T07:49:02.836Z] started tests=252bb4afe34a

- [2026-11-15] 实现：新增 `scheduler/fire.py`（唯一 occurrence Fire 台账 + 冻结 Execution + 进度推进，同一事务；`ON CONFLICT` 处理 Fire/幂等键冲突），重写 `scheduler/service.py`（`(next_fire_at, tenant_id)` 有界扫描 → 锁外 Admission/设置 → `FOR UPDATE` 复核 `status/current_revision_id/next_fire_at` → `write_fire`）。
- [2026-11-15] 删除 v1 的 `ScheduleService`（创建/更新/暂停/列表）、`build_scheduled_snapshot`、`compute_next_fire_at`、`_next_cron_fire`：它们写的是 TASK-002 已重建掉的 v1 列，v2 等价入口是 Draft+确认与生命周期矩阵，保留只会是第二套真相。
- [2026-11-15] 口径决定（input_json）：Execution 只存本次执行要用的输入，不存整份 Revision——REMINDER `{reminder_text, context}`；SKILL `{skill_id, input(按本次计划时刻渲染后的模板), context}`；AGENTIC `{goal, budget, output_contract, context}`。TASK-016/019 的执行器直接消费它，Skill artifact 取本次准入冻结的 Snapshot。
- [2026-11-15] 口径决定（临时失败阈值）：设计只写「连续 N 次」，代码事实是 `fire.py` 的 `TRANSIENT_FAILURES_BEFORE_ATTENTION`，按最近连续 `FAILED_ADMISSION` Fire 计数；ONCE 不受阈值影响（一次有界重试后即 `ENDED(ADMISSION_FAILED)`）。
- [2026-11-15] 口径决定（DST）：`next_fire_at` 是 UTC 指针，经 `astimezone` 还原必然是**存在**的本地时刻，因此 gap 不会成为指针形态；gap/second-fold 由 recurrence 作为本次计划的 `dst_events` 与 `slots_consumed` 给出，`write_fire` 在同一事务写 SCHEDULE 事件并按本地槽计数，不伪造 Fire —— 与设计 §3.5.2 的 gap 分支等价。
- [2026-11-15] 迁移三个 v1 词汇用例：`test_scheduler.py`（时区推进/模板渲染/一轮多条/sweep 节拍/快照 v4）、`test_scheduler_misfire.py`（SKIPPED(MISFIRE) 台账 + `ENDED(MISSED)`）、`test_schedule_trigger.py`（真实 Console 物料解析、双 Scheduler 竞态、pause/cancel/update/delete 赢竞态、撤权永久失败）。v1 的创建/列表/归属断言由 Draft+确认与生命周期矩阵用例按 v2 覆盖。
- [2026-11-15] 指标 label 改为 Fire 状态（`EXECUTION_CREATED`/`SKIPPED`/`FAILED_ADMISSION`），`scheduled_misfire_total` 语义不变。
- [2026-11-15] 遗留（交回各自 owner）：`application/schedule_quota.record_quota_skip` 已无生产调用方（Fire 写入统一走 `scheduler/fire.py`），其单测仍在；`tests/agent_runtime/test_capacity_webchat_clock_lease.py`、`tests/agent_worker/test_task_queries.py`、`tests/agent_worker/test_worker_metrics.py` 仍用 v1 词汇（TASK-002 重建 schema 后已红，现在其中两处改为导入失败），需其 owner 迁移。

### Verification

- 声明测试：`code-flow task finish` 通过（46 passed，含新增 `tests/agent_worker/test_scheduled_v2_fire.py` 与迁移后的三个调度用例集）。
- 业务场景（S-04/S-07/S-08、E-02/E-03/E-06/E-10/E-13、B-01..B-04）：未宣称通过，由需求级终验与 E2E 执行。

<a id="task-015"></a>
- 2026-11-15T08:04:41.965Z PASS commands=1/1 executed=1 deduped=0 elapsed=26663ms tests=252bb4afe34a head=9ea6d0e89580 dirty=1
  - related PASS exit=0
## TASK-015: 改造 claim、lease 与三类外部等待

- **Status**: done
- **Depends**: TASK-003, TASK-005, TASK-014
- **Source**: scheduled-task-v2.backend.design.md §3.3.6、§3.3.12、§3.5.3、§3.5.6
- **Scenarios**: S-06, S-08, S-17, E-03, E-06, E-15, B-05, B-06, B-08, B-12
- **Spec-Refs**: worker/harness-worker, persistence/harness-data, snapshot/harness-snapshot, test/harness-test
- **Paths**: apps/agent-worker/src/muad_agent_worker/worker/**, apps/agent-worker/src/muad_agent_worker/application/batch_*, apps/agent-worker/src/muad_agent_worker/application/terminal_tasks.py, tests/agent_worker/test_scheduled_v2_claim.py, tests/agent_worker/test_worker_leases.py, tests/agent_worker/test_task_deadline.py, tests/agent_worker/test_batch_fanin.py, tests/agent_worker/test_batch_fanout.py
- **E2E**:
- **Skip-Tests**: false
- **Skip-Reason**:

### Tests

```json
[
  {
    "id": "related",
    "argv": [
      "uv",
      "run",
      "python",
      "-m",
      "pytest",
      "-q",
      "tests/agent_worker/test_scheduled_v2_claim.py",
      "tests/agent_worker/test_worker_leases.py",
      "tests/agent_worker/test_task_deadline.py",
      "tests/agent_worker/test_batch_fanin.py",
      "tests/agent_worker/test_batch_fanout.py"
    ],
    "timeout": 600,
    "parallel": false
  }
]
```

### Description

将 QUEUE/有限并行落实到 PG claim：SQL 预筛避免阻塞队头空转，Schedule 锁内复检保证不超发；等待释放执行 lease，但仍按策略占用 Schedule 并发槽。

### Checklist

- [x] QUEUE 按 scheduled_at/create_time FIFO；立即任务继续 priority/create_time；PARALLEL 按配置计算槽位，不用错误的 NOT EXISTS 禁止全部并行。
- [x] 区分 Fire 阻断集与 claim 占槽集；排除候选自身、Child 及同执行续接，不让 QUEUED 自己阻塞自己；被挡住不伪造 not_before 退避。
- [x] 首次领取写 started_at，reclaim/续接不清空；真实重试 attempt 与 HUMAN/EXTERNAL resume_count 分开，lease_epoch fence 所有旧结果。
- [x] WAITING_EXTERNAL 按 ASYNC_OPERATION/EXTERNAL_POLL/BATCH_FANIN 分发；后两者迁移既有轮询和 fan-in，不丢 BATCH 崩溃恢复/幂等扇出。
- [x] claim、心跳、终态均检查 deadline 与有效 owner/epoch/lease；心跳退出监督本地执行取消；deadline sweep 独立于慢的 due scan。
- [x] 真实双 Worker 验证 FIFO、max_concurrency、不同 Schedule 不饥饿、等待不占本地执行槽、reclaim 后不重放副作用，锁序无反转。

### Spec Delta

```json
[]
```

### Log

- [2026-11-15] created (draft)
- [2026-11-15T08:08:42.577Z] started tests=ef0ee8dfdda5
- [2026-11-15] 实现：重写 `worker/claimer.py` 为**两段式领取**——①无锁预筛（占槽计数 + `wait_kind` 分发写进 SQL，避免被挡住的队头空转）；②固定锁序 Schedule → Execution，在 Schedule 行锁内复检占槽数并 `FOR UPDATE SKIP LOCKED` 改状态。排序：调度任务 `(scheduled_at, create_time)`（`priority` 不参与），立即任务 `(priority, create_time)` 且先于调度队列。
- [2026-11-15] 口径决定（两套集合）：占槽集 = `RUNNING/WAITING_EXTERNAL/WAITING_HUMAN`，**不含 QUEUED**（`worker/claimer.SLOT_STATUSES`）；阻断集仍是 `scheduler/fire.BLOCKING_ROOT_STATUSES`（含 QUEUED）。占槽子查询排除候选自身与全部 Child（`parent_id IS NULL`），"自己的 WAITING_EXTERNAL 续接"正是候选行本身，因此不占新槽；被挡住的候选不写 `not_before`。
- [2026-11-15] 口径决定（并发上限来源）：按 Schedule 的**当前 Revision** `schedule_policy` 计算槽位（`ALLOW_PARALLEL` → `max_concurrency`，其余 1）；上限不写进 Execution，避免一份可变的第二真相。
- [2026-11-15] 口径决定（三类等待的唤醒源）：`EXTERNAL_POLL` 到 `not_before` 由定时 claim 续接；`ASYNC_OPERATION` **不看定时器**，只有 `wait_payload_json.operations[]` 全部终态（`COMPLETED/FAILED/CANCELLED`，用 `jsonb_path_exists` 判定）才续接，结果水位是唯一真相；`BATCH_FANIN` 永不 claim——Parent 由 Child 终态在 fan-in 事务里直接置终态。缺 `wait_kind` 的 `WAITING_EXTERNAL` 不盲领。
- [2026-11-15] 计数口径：首次/真实重试（QUEUED→RUNNING）计 `attempt` 并只在 `started_at IS NULL` 时写 `started_at`；HUMAN/EXTERNAL 续接只计 `resume_count` + `last_resume_reason`，不清 `started_at`；每次领取都推进 `lease_epoch`（fence 掉旧结果），`wait_kind` 在续接时清空、`wait_payload_json` 保留给执行器读 `external_ref`。
- [2026-11-15] deadline 三侧收口：claim 排除过期行；心跳 `LeaseRenewal.expired` 叫停本地执行并落 `FAILED(TASK_DEADLINE_EXCEEDED)`（新增 `LeaseRenewal`，一次行读取同时给 owner/取消/期限）；COMPLETED 的终态 CAS 增加 `deadline_at > moment`；回收遇到已过期执行不再重排，直接 `FAILED(TASK_DEADLINE_EXCEEDED)`。显式取消优先于期限（CANCELLED 优先，二者都不许写成 COMPLETED）。deadline sweep 仍由 Scheduler 独立节拍驱动。
- [2026-11-15] 等待载荷迁移到 v2 列：`_wait` 写 `wait_kind` + `wait_payload_json`（`external_ref` / `operations[]` / `batch`），Skill 的 `wait` 信封新增可选 `wait_kind`（缺省 `EXTERNAL_POLL`），批量信封显式声明 `BATCH_FANIN`；`batch_fanout` 改写 `wait_payload_json` 并去掉 v1 的 `external_ref_json`/`delivery_*`/`intent_key`/`execution_mode` 列；终态写清 `wait_kind` 但保留载荷（取消路径在终态后仍按 `operations[]` 发远端取消意图）。
- [2026-11-15] 事件口径：v1 的 `FAN_OUT`/`FAN_IN` 不在 §3.5.10 的事件全集（`TaskEventType` 里已不存在），批量扇出不再写专用事件；聚合明细随 Parent 终态事件的 payload 落库。
- [2026-11-15] 测试：新增 `tests/agent_worker/test_scheduled_v2_claim.py`（16 例：FIFO、ALLOW_PARALLEL 真槽位、等待占槽、被挡队头不饿死别的 Schedule、锁内复检拒绝陈旧候选、双 Worker 同时通过预筛只领一条、多 Worker 并发不重复领取、Schedule 先锁/执行行 `SKIP LOCKED`、三类等待的领取口径、等待不占本地执行槽）；`test_worker_leases.py` 补 started_at/epoch/resume_count/回收不重放不可重试副作用；`test_task_deadline.py` 补心跳叫停跨期限执行、回收不重排过期执行、取消优先于期限，并按 v2 列与 `task_delivery` 投递意图重写 E-03；`test_batch_fanin.py`/`test_batch_fanout.py` 对齐 `WAITING_EXTERNAL`/`BATCH_FANIN`。
- [2026-11-15] 遗留（交回各自 owner）：`tests/agent_worker/test_task_lease_epoch.py` 仍按 `_renew_lease` 的两元组解包（现返回 `LeaseRenewal`，该文件本就因 v1 列而红，归 TASK-020）；`tests/agent_worker/test_worker_outcomes.py`（TASK-016）与 `tests/agent_worker/test_worker_metrics.py`（导入失败的 `ScheduleService`）仍是 v1 词汇。

### Verification

- 声明测试：`code-flow task finish` 通过。
- 业务场景（S-06/S-08/S-17、E-03/E-06/E-15、B-05/B-06/B-08/B-12）：未宣称通过，由需求级终验与 E2E 执行。

<a id="task-016"></a>
- 2026-11-15T08:28:14.937Z PASS commands=1/1 executed=1 deduped=0 elapsed=35044ms tests=ef0ee8dfdda5 head=bf9d45a9d53c dirty=1
  - related PASS exit=0
## TASK-016: 接通 Reminder、Skill 与现有立即任务执行链

- **Status**: done
- **Depends**: TASK-008, TASK-010, TASK-015
- **Source**: scheduled-task-v2.backend.design.md §2.3.2、§3.2.2、§3.3.6、§3.5.6
- **Scenarios**: S-01, S-02, S-04, S-07, E-02, B-03, B-08, B-09
- **Spec-Refs**: worker/harness-worker, artifact/harness-skill, snapshot/harness-snapshot, secret/harness-secret, test/harness-test
- **Paths**: apps/agent-worker/src/muad_agent_worker/worker/**, apps/agent-worker/src/muad_agent_worker/application/task_service.py, apps/agent-runtime/src/muad_agent_runtime/application/task_client.py, apps/agent-runtime/src/muad_agent_runtime/application/async_tools/**, packages/contracts/src/muad_contracts/snapshot.py, tests/agent_worker/test_scheduled_v2_executors.py, tests/agent_worker/test_task_executor.py, tests/agent_worker/test_skill_argv.py, tests/agent_worker/test_task_defaults_from_settings.py
- **E2E**:
- **Skip-Tests**: false
- **Skip-Reason**:

### Tests

```json
[
  {
    "id": "related",
    "argv": [
      "uv",
      "run",
      "python",
      "-m",
      "pytest",
      "-q",
      "tests/agent_worker/test_scheduled_v2_executors.py",
      "tests/agent_worker/test_task_executor.py",
      "tests/agent_worker/test_skill_argv.py",
      "tests/agent_worker/test_capacity_webchat_retry_safety.py",
      "tests/agent_worker/test_worker_outcomes.py",
      "tests/agent_worker/test_task_defaults_from_settings.py",
      "tests/agent_runtime/test_capacity_webchat_clock_lease.py"
    ],
    "timeout": 600,
    "parallel": false
  }
]
```

### Description

使新 schema 下的 REMINDER/SKILL 与已有立即 Skill、Parent/Child BATCH 完整可执行；统一更新实际生产者与消费者，保留既有业务能力，去掉旧协议字段。

### Checklist

- [x] ReminderExecutor 直接持久化固定文本；断言零模型、零 Skill、零工具调用；结果先落库，投递由独立边界处理。
- [x] SkillExecutor 精确使用冻结 artifact/checksum、argv 与 input，Fire resolve latest、重试不漂移；缓存缺失按原产物协议读取，不重新理解自然语言。
- [x] Runtime TaskClient/ASYNC 提交、Worker create-task、Child fan-out 同步写新 task_type/skill_execution_mode/Snapshot；不继续发送 intent_key 或旧 delivery 字段。
- [x] Skill result/wait 强校验，EXTERNAL_POLL 显式设置等待载荷；非法返回确定性失败，不留无心跳 RUNNING。
- [x] 回归 JOIN/DETACH、单 call 身份、CLI 参数、副作用 retry safety、BATCH fan-in 与 lease/deadline；不新增兼容 serializer 或旧状态映射。

### Spec Delta

```json
[]
```

### Log

- [2026-11-15] 承接 `tests/agent_worker/test_task_defaults_from_settings.py`：TASK-002 的 schema 重建（`intent_key`/旧 delivery 列移除）让它在中间态 7 failed，TASK-005 把它从自己的声明测试里移出后无人认领；本任务改执行链时一并把它迁到新协议。
- [2026-11-15] created (draft)
- [2026-11-15] `worker/reminder.py` 新增 `ReminderExecutor`（零模型/零 Skill/零工具），`worker/dispatch.py` 新增 `TaskTypeExecutor` 按 `task_type` 分派；REMINDER 结果固定为 `{"text": <原文>}`，缺文本落 `REMINDER_TEXT_MISSING` 确定性失败，未接入的 AGENTIC 显式 `TASK_TYPE_UNSUPPORTED` 而不是降级成 Skill。结果由既有终态事务先写 `result_json`，投递是独立边界。
- [2026-11-15] 立即任务协议迁移：`CreateTaskRequest` 去掉 `intent_key`/`delivery_mode`，新增 `task_type`/`skill_execution_mode`（SKILL 才要求 skill 列）；Runtime `WorkerTaskClient.submit_task` 与 ASYNC `_submission` 同形发送；Worker `TaskService` 写 `task_type`/`skill_execution_mode`，并把创建时的 `delivery_route` 落成独立 `task_delivery` 意图（`execution:{id}:final`），不再写六个已移除的执行行投递列；Child fan-out 改用 contracts 的 `TaskType`/`SkillExecutionMode`。
- [2026-11-15] 修 `POST /internal/tasks` 的过期调用：`submission_fingerprint(ENDPOINT_CREATE_TASK, body)` 用的是已改签名（keyword-only），每条 create-task 都抛 TypeError；改回 `endpoint/tenant_id/actor_user_id/action` 关键字形态。
- [2026-11-15] 迁移 `tests/agent_worker/test_task_defaults_from_settings.py`：调度默认改走 v2 `SchedulerLoop` + 真实 Fire，misfire 的判据是 FIRE 行 `status=SKIPPED, skip_reason=MISFIRE`（不是 Schedule 的 `last_error_code`）；新增立即任务 v2 列与 `task_delivery` 意图断言、Child fan-out 形状断言。**删除两例投递断言**（locale / 退避上限）：执行行的 `delivery_status/delivery_attempts` 事实已不存在，投递默认归 TASK-022 的 `delivery/**`，不以已删除列重写。
- [2026-11-15] 迁移 `tests/agent_runtime/test_capacity_webchat_clock_lease.py`：Schedule 触发改成种 Schedule+Revision 后用 `resolve_schedule_materials` 替身跑真实 `SchedulerLoop`；`_renew_lease` 断言改读 `LeaseRenewal`（owned/cancel_requested）；`_seed_task` 去掉 intent_key 与旧 delivery 列。
- [2026-11-15] 同步迁移既有断言到 v2 事实：`WAITING`→`WAITING_EXTERNAL`、`external_ref_json`→`wait_kind`+`wait_payload_json`（test_worker_outcomes）、TaskExecution 夹具去 intent_key（test_task_executor/test_skill_argv/test_capacity_webchat_retry_safety）、事件名 QUEUED 与投递意图表（test_task_service）、JOIN 不建投递行（test_task_handoff）、新 CreateTaskRequest 形状（tests/test_contracts、contracts/test_runtime_strict_json、test_task_contracts）；按新增/删除的模块级常量重算 `docs/configuration-inventory.csv`。
- [2026-11-15] 新增 `tests/agent_worker/test_scheduled_v2_executors.py`（9 例）：REMINDER 零依赖与确定性失败、分派、冻结 argv/input、重试不漂移、artifact 字节被换即拒、缓存缺失按产物协议、BATCH 父任务只等 fan-in。
- [2026-11-15] 未纳入本任务：`delivery/**` 仍读已移除的执行行投递列（`DeliveryLoop` 与 `messages.py` 的 `intent_key`），按计划由 TASK-022 以 `task_delivery` 为唯一状态源重做；`api/runtime_operations.py` 等处的 `submission_fingerprint` 旧调用不在本执行链上，未改动。
- [2026-11-15T08:30:10.389Z] started tests=1641b08d9ea6

### Verification

- 尚未执行；此 TASK 的声明测试、业务场景均未宣称通过。

<a id="task-017"></a>
- 2026-11-15T08:48:04.574Z PASS commands=1/1 executed=1 deduped=0 elapsed=41489ms tests=b9a2a3fac61e head=0f6978878eb3 dirty=1 tests-changed
  - related PASS exit=0
## TASK-017: 以自然语言生成 Draft 并发送专用确认事件

- **Status**: done
- **Depends**: TASK-011, TASK-012, TASK-013
- **Source**: scheduled-task-v2.backend.design.md §2.3.2、§2.3.9、§3.2.8、§3.5.4
- **Scenarios**: S-01, S-02, S-03, S-04, S-05, S-06, E-01, E-05, E-14, B-07
- **Spec-Refs**: api/harness-api, auth/harness-auth, im/harness-im, snapshot/harness-snapshot, secret/harness-secret, test/harness-test
- **Paths**: apps/agent-runtime/src/muad_agent_runtime/application/task_tools.py, apps/agent-runtime/src/muad_agent_runtime/application/task_client.py, apps/agent-runtime/src/muad_agent_runtime/application/time_tools.py, apps/agent-runtime/src/muad_agent_runtime/application/executor.py, packages/contracts/src/muad_contracts/webchat_events.py, tests/agent_runtime/test_scheduled_v2_intent_tools.py, tests/agent_runtime/test_scheduled_v2_draft_events.py
- **E2E**:
- **Skip-Tests**: false
- **Skip-Reason**:

### Tests

```json
[
  {
    "id": "related",
    "argv": [
      "uv",
      "run",
      "python",
      "-m",
      "pytest",
      "-q",
      "tests/agent_runtime/test_scheduled_v2_intent_tools.py",
      "tests/agent_runtime/test_scheduled_v2_draft_events.py",
      "tests/contracts/test_capacity_webchat_event_contracts.py",
      "tests/architecture/test_channel_neutrality.py"
    ],
    "timeout": 600,
    "parallel": false
  }
]
```

### Description

重构已有 create/update schedule 工具为只生成 Draft；查询、暂停、恢复、取消走已授权生命周期服务。模型负责候选解释，服务端验证；确认之后无需模型再调用创建。

### Checklist

- [x] 固定文本选择 REMINDER、明确 Skill 参数选择 SKILL、组合工具选择 AGENTIC；三者都经 Draft；不暴露模型可调用的 confirm/create 生效工具。
- [x] 复用时间工具与用户 IANA 时区，缺字段澄清；同名多匹配返回候选要求指定；多个客户/独立目标拆成多个各自确认的 Schedule。
- [x] 同回合写 canonical schedule.draft.ready，包含 draft_id/hash/脱敏预览/expiry；重放已持久化事件，不复用 Run confirmation.required/interrupt/resume。
- [x] UPDATE 显示前后差异，生命周期命令携带稳定幂等身份；ENDED ONCE 引导新建，不改原时间或恢复。
- [x] 事件 payload 进入共享投影/历史重建链，不能只在当次 SSE 内临时生成；覆盖刷新、重连、重复工具调用、自确认攻击与 Secret。
- [x] 同步更新既有 task tools/client、Prompt 及事件投影测试的 Draft 语义；此任务是 Runtime 完整回归的前置，不依赖后续任务中心页面或读 API。

### Spec Delta

```json
[]
```

### Log

- [2026-11-15] created (draft)
- [2026-11-15] 模型面写路径收敛为「只产 Draft」：`create_schedule`/`update_schedule`/`delete_schedule` 三个工具被 `draft_schedule`（CREATE）/`draft_schedule_update`（UPDATE）与 `pause_schedule`/`resume_schedule`/`cancel_schedule` 取代；`WorkerTaskClient` 的 `create_schedule/update_schedule/delete_schedule` 换成 `create_schedule_draft`（API-01，带 `X-Agent-Id`/`X-Entry-Channel`）、`schedule_action`（API-04/05/06）、`get_schedule`。客户端里**没有**确认方法，确认只能由 surface 在用户显式动作后调 API-27；自确认用例断言 409 `SCHEDULE_CONFIRMATION_REQUIRED` 且 Draft 仍 PENDING。
- [2026-11-15] 模式与澄清：REMINDER/SKILL/AGENTIC 由 `mode` 判别，各自的专属字段出现错位即显式拒绝（不静默丢）；SKILL 只认本 Run 的有效 `skill_key`（映射到 `skill_id`），AGENTIC 的 allowlist 必须非空并逐个映射（SKILL 项用 `skill_key`，MCP 项用 `server_key`）。时区必须是用户确认过的 IANA 名：缺失 → `SCHEDULE_TIMEZONE_REQUIRED`（提示去问用户），非法 → `SCHEDULE_DRAFT_INVALID{reason=INVALID_TIMEZONE}`；校验复用 `time_tools.resolve_zone`，不回退服务端时区。`context.parameters` 里出现多值列表 → `SCHEDULE_DRAFT_INVALID{reason=MULTIPLE_TARGETS}` 并提示拆成多条 Draft（§2.3.9）。
- [2026-11-15] 投递意图：Runtime 按共享通道词表（`OUTBOUND_CHANNELS`/`LOCAL_STREAM_CHANNELS`）把入口 surface 映射成 `delivery_intent` 并透传 `X-Entry-Channel`，核心域不点名任何渠道（通道中立守卫 64 passed）；Draft 的 delivery 与入口能力由 Worker 再核一次。
- [2026-11-15] 确认卡片：新增 `application/schedule_draft_events.py` 的 `publish_schedule_draft_ready`（`event_type=SCHEDULE_DRAFT_READY`、`stream_type=schedule.draft.ready`、`source_event_id=uuid5(run,draft,hash)`），事务只写一条 canonical 行，提交后唤醒追尾订阅者；没有会话事件 sink 时显式降级（warning + 工具回执 `card=UNPUBLISHED`），不把已落库的 Draft 报成失败。Runtime 的 `public_events` 白名单与投影新增该类型，历史查询按 `event_type+stream_type` 这一对准入并把卡片挂在空正文助手条目上（刷新后仍可确认）；契约新增 `WebchatScheduleDraftReady`（`webchat.py`）+ `schedule.draft.ready` 事件变体（`webchat_events.py`），`SchedulePreview` 补 `diff` 以承载 UPDATE 前后差异；历史条目上的 `draft` 用 `exclude_if(None)`，普通消息的线格式不因此多一个 `draft: null`（既有精确断言保持原样）。
- [2026-11-15] 生命周期：目标用 `schedule_id` 或名字/目标子串匹配；零命中 `SCHEDULE_NOT_FOUND`、多命中 `SCHEDULE_AMBIGUOUS`（回候选，不静默挑一个）；终态（ENDED/CANCELLED）拒绝 UPDATE/RESUME 并给 `repair=CREATE_NEW`；动作幂等身份取 `run:{run}:call:{call_id}`（同一次工具调用重试重放，换一次调用即另一条命令，以 `task_submission` 身份条数断言）。
- [2026-11-15] 同步既有测试：`tests/agent_runtime/test_task_handoff.py` 的 Schedule 用例改为 Draft 语义（Draft 幂等 → 用户确认 → 生命周期越权 404），并补 `task_schedule_draft`/`task_schedule_revision` 的租户清扫；`time_tools` 的文档串引用改为 `draft_schedule`（仓库里没有写死 schedule 工具名的 Prompt）。该文件仍有 4 个**与本任务无关**的失败：Worker `POST /internal/tasks` 还在用旧的 `submission_fingerprint(endpoint, body)` 签名（TASK-016 的执行链迁移范围内），非本次改动引入。
- [2026-11-15] 未覆盖/交接：WebChat 侧的 SSE 公开投影在 `im-gateway`（`sse.KNOWN_EVENT_TYPES` + `webchat_streams.PUBLIC_STREAM_EVENT_TYPES` + projector 分支）与 Draft 卡片渲染在 TASK-024/TASK-026；本任务只保证 Runtime 侧持久行、共享投影、历史与契约就位。UPDATE 需要模型给出**完整**新配置（Worker 的读面还不返回当前 Revision，全量详情读属 TASK-023）。
- [2026-11-15T07:49:07.919Z] started tests=bcbcebd3278b

### Verification

- 尚未执行；此 TASK 的声明测试、业务场景均未宣称通过。

<a id="task-018"></a>
- 2026-11-15T08:04:45.690Z PASS commands=1/1 executed=1 deduped=0 elapsed=15185ms tests=bcbcebd3278b head=9ea6d0e89580 dirty=1
  - related PASS exit=0
## TASK-018: 切换 Runtime 生产路径并通过完整相关回归

- **Status**: done
- **Depends**: TASK-003, TASK-009, TASK-016, TASK-017
- **Source**: scheduled-task-v2.backend.design.md §3.1.2、§3.5.6、§4.2 阶段 2
- **Scenarios**: S-03, S-09, S-15, S-16, S-17, B-08, B-09
- **Spec-Refs**: snapshot/harness-snapshot, memory/harness-memory, model/harness-model, mcp/harness-mcp, secret/harness-secret, test/harness-test
- **Paths**: apps/agent-runtime/src/muad_agent_runtime/application/**, apps/agent-runtime/src/muad_agent_runtime/main.py, tests/agent_runtime/**, tests/architecture/test_scheduled_v2_kernel_boundaries.py
- **E2E**:
- **Skip-Tests**: false
- **Skip-Reason**:

### Tests

```json
[
  {
    "id": "runtime-regression",
    "argv": [
      "uv",
      "run",
      "python",
      "-m",
      "pytest",
      "-q",
      "-m",
      "not capacity_load",
      "--durations=30",
      "tests/agent_runtime"
    ],
    "timeout": 1800,
    "parallel": false
  }
]
```

### Description

把 Runtime 现有 executor 的装配收敛为 Runtime ExecutionAdapter，真实 Run 生产路径使用 TASK-009 的中立端口。此任务是 Worker Agentic 接入的硬依赖；不允许保留两套可切换的循环。

### Checklist

- [x] RunRecord、Conversation、canonical_event、SSE、Memory 写入/确认与 Run lease 继续由 Runtime adapter 持有；核心循环不感知宿主。
- [x] 保留 Model Pool/实时凭据/容量租约、冻结策略、MCP/SYNC/ASYNC Skill、压缩/结果外置、预算/取消与 answer verifier。
- [x] 新建 tests/agent_runtime/test_scheduled_v2_runtime_adapter.py，执行共享端口契约，并验证真实工厂与 bootstrap 确实装配新适配器。
- [x] 验证 Run resume 使用原 Snapshot、Memory 确认/ASYNC continuation 不丢效果、SSE 序号与事件封套不漂移；移除旧装配旁路与 stub。
- [x] 本任务仅此处扩大到整个 tests/agent_runtime 相关 unit/integration 目录；命令必须无失败且关键测试不因环境缺失跳过，记录实际结果后才 finish。
- [x] 故障注入覆盖摘要失败、批量 artifact 部分写失败、失约及 deadline；确认原历史/inline 保留，降级日志与指标可见，主 Run 结果不被压缩错误改变。

### Spec Delta

```json
[]
```

### Log

- [2026-11-15] created (draft)
- [2026-11-15T08:50:09.799Z] started tests=f6c1ce1c8e62
- [2026-11-15] 完整相关回归（本任务的声明测试）实际结果：`uv run python -m pytest -q -m "not capacity_load" --durations=30 tests/agent_runtime` → **1425 passed, 0 failed in 745.89s**（exit 0；`tests/agent_runtime/test_scheduled_v2_runtime_adapter.py` 与前一轮已修的 9 例均已包含在该计数内）。无 skip 因环境缺失而吞掉关键断言。
- [2026-11-15] 修 Runtime 全目录回归打出的 v2 漂移：Console `overview_sql.py` 仍在 `task.task_execution` 上投影 `delivery_mode`/`delivery_status`（v2 已移除，整条概览 SQL 直接 UndefinedColumn），`failed_deliveries` 改按 `task.task_delivery` + 本周期根执行聚合（投递意图已是独立权威），`waiting_tasks`/`overdue_tasks` 改用 `WAITING_EXTERNAL`/`WAITING_HUMAN`。这让 `tests/agent_runtime/test_shared_egress_audit_store.py` 的 8 例恢复（概览端点原本完全不可用）。
- [2026-11-15] 复跑结果：`tests/agent_runtime` 全目录从 9 failed / 1405 passed 修到 0 failed（保留清理夹具 + 概览 SQL 两处根因）；`tests/architecture` 239 passed，`ruff check` 全绿。剩余 Console 概览面 6 例（`tests/console_platform/test_overview_api.py`）仍红：其夹具仍用 `intent_key`/`delivery_*`/`TaskSchedule.actor_user_id` 这些 v2 已移除的形状，需随该面（计划外、无人认领）的夹具迁移收口；本任务未改该测试面。
- [2026-11-15] 生产路径复核：Runtime 生产代码只有一处 `AgentRunner(...)` 装配（`application/executor.py::_agent_runner`），接的是中立端口（`tool_suspension`/`runtime_events`/`context_compactor`/`round_results`/`tool_pipeline`）；`main._build_executor_factory` 只是把分池/客户端注入 `default_executor_factory`，装配根没有第二份执行器工厂，也没有 stub 旁路。
- [2026-11-15] 新增 `tests/kernel_adapter_contract.py`：宿主无关的 Kernel 契约（完整回合边界挂起、中断 reason 透传、越权工具、工具预算、取消优先、按宿主落库的 checkpoint 恢复不重跑）；Runtime 与 Worker 适配器共用同一组用例，宿主差异只在 `HostAdapter`。
- [2026-11-15] 新增 `tests/agent_runtime/test_scheduled_v2_runtime_adapter.py`（11 例）：6 个共享契约用例绑定**真实** `MemoryConfirmationService` + 分离克隆库 + 真实 `DurableRuntimeEventPort`（真落 `RunInterrupt`/canonical 事件/`RunContinuation`）；另验证挂起落库（Run `WAITING_INPUT`、租约释放、checkpoint 钉在冻结 `snapshot_id`、`run.stage` 先于确认事件且封套字段齐全）、生产工厂装配（中立端口 + 压缩/结果外置/工具策略）、生产工厂 + 真实 HTTP 模型端到端的等待事实、Runtime 生产代码只有一处 Agent 循环。
- [2026-11-15] `tests/architecture/test_scheduled_v2_kernel_boundaries.py` 增加两条边界：Kernel 不引用任何宿主实体（`RunRecord`/`CanonicalEvent`/`RunInterrupt`/`TaskExecution`…）；`MemorySuspendRequest` 只允许出现在 Runtime 适配器的 `memory_suspension`/`memory_confirmation`/`checkpoint` 三个模块里。
- [2026-11-15] 迁移 `tests/agent_runtime/test_capacity_webchat_retention.py` 的任务执行夹具到 v2 列（去 `intent_key`/`delivery_key`/`execution_mode`，改 `task_type`/`skill_execution_mode`/`trigger_type=IMMEDIATE`）；断言未放宽（活跃引用仍不进候选、宽限期内跳过、过期无引用才删）。
- [2026-11-15] 故障注入由本目录既有用例覆盖并随整目录回归通过：摘要失败降级（`test_capacity_webchat_background_pool::test_e06_summary_failure_keeps_original_history_and_visible_degradation`、`test_summary_model_resolution::test_unresolvable_summary_model_keeps_the_run_alive_and_skips_the_layer`）、整轮 artifact 回滚（`test_artifact_round_budget::test_round_batch_rolls_back_every_file_it_wrote`）、失约/期限（`test_cancellation`、`test_resume_transactions` 的 deadline 不延期、`test_capacity_webchat_clock_lease`）。
- [2026-11-15] 发现的遗留缺口（不属本任务，未改）：`apps/console-platform/.../artifact_retention_repository.py` 的活跃执行引用谓词仍用 v1 状态 `'WAITING'`，而 v2 只有 `WAITING_EXTERNAL`/`WAITING_HUMAN` ⇒ 等待外部/人类的执行所引用的产物会失去保护；`apps/console-platform/.../overview_sql.py` 的 `waiting_tasks` 与超期统计同样用 `'WAITING'`。前者应由 TASK-031（保护等待中的执行）收口，后者随 TASK-030/TASK-032 的口径同步，本任务只在 Runtime 回归范围内迁移了测试夹具。

### Verification

- 尚未执行；此 TASK 的声明测试、业务场景均未宣称通过。

<a id="task-019"></a>
- 2026-11-15T09:32:51.235Z PASS commands=1/1 executed=1 deduped=0 elapsed=753099ms tests=f6c1ce1c8e62 head=9738ed87bc16 dirty=1
  - runtime-regression PASS exit=0
## TASK-019: 实现完整 Worker Agentic ExecutionAdapter

- **Status**: done
- **Depends**: TASK-006, TASK-007, TASK-008, TASK-018, TASK-010, TASK-016
- **Source**: scheduled-task-v2.backend.design.md §3.1.2、§3.2.4、§3.5.6
- **Scenarios**: S-03, S-15, S-16, S-17, E-10, B-08, B-09
- **Spec-Refs**: worker/harness-worker, snapshot/harness-snapshot, memory/harness-memory, model/harness-model, mcp/harness-mcp, secret/harness-secret, auth/harness-auth, test/harness-test
- **Paths**: apps/agent-worker/src/muad_agent_worker/worker/**, apps/agent-worker/src/muad_agent_worker/application/**, apps/agent-worker/src/muad_agent_worker/infrastructure/**, apps/agent-worker/src/muad_agent_worker/main.py, tests/agent_worker/test_scheduled_v2_agentic_adapter.py, tests/agent_worker/test_context_compaction_absent.py, tests/architecture/test_scheduled_v2_kernel_boundaries.py
- **E2E**:
- **Skip-Tests**: false
- **Skip-Reason**:

### Tests

```json
[
  {
    "id": "related",
    "argv": [
      "uv",
      "run",
      "python",
      "-m",
      "pytest",
      "-q",
      "tests/agent_worker/test_scheduled_v2_agentic_adapter.py",
      "tests/agent_worker/test_context_compaction_absent.py",
      "tests/architecture/test_scheduled_v2_kernel_boundaries.py",
      "tests/architecture/test_channel_neutrality.py"
    ],
    "timeout": 600,
    "parallel": false
  }
]
```

### Description

Runtime 生产路径门槛通过后接入 Worker AgenticExecutor；与 Runtime 使用同一 Kernel、同一参数化行为契约，宿主只持有 TaskExecution 与 Worker lease，不创建 Run 或第二套执行权威。

### Checklist

- [x] 装配冻结 Model Pool/summary model、实时凭据与 BACKGROUND capacity、完整 Prompt/Context、MCP 与 SYNC/ASYNC Skill tools、output contract/verifier、压缩及 Task artifacts。
- [x] ToolRegistry 仅构造 allowlist 内工具，每次调用经过冻结 Tool Policy 和有效能力检查；未授权工具不产生外部请求；恶意 Context 不能覆盖系统策略。
- [x] 仅注入 TASK-010 冻结的 READ_ONLY 记忆块，不注册记忆写工具/提炼作业，不在每轮或恢复时刷新为当前内容。
- [x] task_event 持久化模型/工具/状态；每回合写 checkpoint、预算累计和稳定 tool_call_id，支持持久 Human/External 挂起结果交给后续 manager。
- [x] 模型/工具回合检查 cancel/epoch/lease/deadline，失约协作终止；非幂等副作用崩溃按 TASK_RESULT_UNVERIFIED 收尾，不自动重放。
- [x] 完整覆盖压缩降级与整轮 artifact 回滚、长结果外置、并行工具、最终产物/错误；更新“Worker 不含压缩”的旧测试，使断言按 SKILL/AGENTIC 实际边界区分。
- [x] 同一套参数化 Kernel 契约分别绑定真实 Runtime/Worker 适配器；禁止 stub、宿主判断分支、import Runtime ORM 或将 Agentic 简化为文本返回。

### Spec Delta

```json
[
  {
    "id": "DELTA-001",
    "op": "update",
    "target": "worker/harness-worker",
    "ref": "RULE-worker-001",
    "before": "PostgreSQL 是 Task/Schedule/lease 的唯一权威源；Redis 仅作 wake-up/cancel hint；`task_type` 仅 `SKILL/BATCH`（外部等待用 WAITING 表达）；claim 使用 `FOR UPDATE SKIP LOCKED`，进入 WAITING 释放 lease，deadline 到期由 Scheduler sweep 置 `FAILED(TASK_DEADLINE_EXCEEDED)`。**deadline 是领取与收尾两侧的硬条件**：claim 排除 `deadline_at <= now` 的行（过期任务绝不执行），执行跨过 deadline 也不得写 `COMPLETED`，同样以 `FAILED(TASK_DEADLINE_EXCEEDED)` 收尾（`worker/claimer.py` 的 `_claimable_conditions`、`worker/service.py` 的终态守卫）。",
    "rule": "PostgreSQL 是 Schedule、Fire、Task Execution 与 lease 的唯一权威，Redis 仅作独立 wake-up/cancel hint。任务执行类型与等待状态由共享 contracts 持有，Reminder、Skill、Agentic 与内部 Batch 使用同一执行权模型；等待释放执行 lease，恢复同一 Execution 不消耗业务重试。Scheduler 在锁外准入、锁内复检，原子写唯一 occurrence Fire、冻结 Execution 与调度进度，Fire 不持有第二套 claim/lease。Worker 使用数据库 claim 与 owner/epoch/有效 lease CAS，按 Schedule 策略原子控制根执行并发及顺序；全局遵守 operation→Schedule→Fire→任务树→事件/发件锁序。claim 与收尾均执行绝对 deadline，过期任务不得执行或提交成功；取消、回收、等待续接及终态共享原子状态/事件边界。",
    "evidence": "scheduled-task-v2.backend.design.md §3.3.6、§3.3.12、§3.5.2、§3.5.3、§3.5.6；TASK-014/TASK-015/TASK-019/TASK-020/TASK-021 与 S-08/S-17/B-08。"
  }
]
```

### Log

- [2026-11-15] created (draft)
- [2026-11-15T09:48:13.540Z] started tests=a546994b38e9
- [2026-11-15] 落地 Worker Agentic 适配器：`application/agentic/**`（快照读视图、模型池+容量+API-09 凭据、allowlist 工具目录、只读记忆、task_event、checkpoint、挂起暂存、压缩、整轮外置）+ `worker/agentic_executor.py`（信封与失败码）+ `worker/agentic_skills.py`（SYNC 沙箱 / ASYNC 提交）+ `infrastructure/{console_credentials,task_operation_client,task_artifact_client}.py`；装配根 `worker/assembly.py` 与 `main.py` 懒加载注入，`TaskTypeExecutor` 增 AGENTIC 分支。
- [2026-11-15] 等待状态仍由 `WorkerLoop` 单点写入：`_wait` 同事务写 checkpoint + WAITING_EXTERNAL；新增 `_wait_human` 同事务写 OPEN 提问 + WAITING_HUMAN + HUMAN/REQUESTED 与 EXECUTION/WAITING_HUMAN 事件。执行期挂起端口只暂存（心跳仍在续租，任何提前改状态都会与续租抢行）。
- [2026-11-15] 共享 Kernel 契约 `tests/kernel_adapter_contract.py` 的 6 条用例绑定到真实 Worker 适配器（真实 PG + 生产 `AgenticTaskExecutor`/`TaskCheckpointStore`；仅模型传输与 Runtime/Console 的容量/操作/产物/凭据在测试里替身）。
- [2026-11-15] 边界守卫更新：`test_context_compaction_absent.py` 从“Worker 无压缩”改为“压缩只在 Agentic 面，SKILL 路径不建模型请求”；`test_scheduled_v2_kernel_boundaries.py` 增 Worker 单一 `AgentRunner` 装配与端口实现检查；`docs/configuration-inventory.csv` 重新按 AST 生成。
- [2026-11-15] 实现期修掉三处真问题：执行器与 Kernel 各建一个挂起端口（导致一次 ASYNC 提交被跑成 N 轮模型回合）；模型 provider 每次调用重建（脚本化传输会重复消费脚本）；取消探针只在轮询节拍后才读（执行前已取消仍会发起首轮模型调用）。
- [2026-11-15] 明确留给后续 TASK 的部分（本 TASK 未宣称）：多 operation 的恢复编排与乱序/迟到结果竞态（TASK-020，本 TASK 的恢复只注入当前已终态的结果并替换同名 tool 消息）；Human 回答、超时与双侧 CAS（TASK-021，本 TASK 只落 OPEN 提问 + WAITING_HUMAN + checkpoint）；Agentic 的 MCP 调用尚未接共享 egress 预算/计量与 `McpBudgetScope`，SYNC Skill 的 egress 预算因 v4 快照未冻结 `budget.egress_budget` 而按契约默认（unlimited）——两处都不伪称已覆盖。S-03/S-15/B-08 场景级 E2E 不在本 TASK 的测试声明里。

### Verification

- 声明测试（4 个文件，36 条）已通过：`task finish` PASS（1/1 command，17.6s）。
- 场景级 E2E（S-03/S-15/S-16/S-17/E-10/B-08/B-09）未在本 TASK 内执行，不宣称通过。

<a id="task-020"></a>
- 2026-11-15T10:16:25.813Z PASS commands=1/1 executed=1 deduped=0 elapsed=18477ms tests=a546994b38e9 head=63d5274950b3 dirty=1
  - related PASS exit=0
## TASK-020: 恢复 ASYNC 多 operation 并消除等待竞态

- **Status**: done
- **Depends**: TASK-007, TASK-015, TASK-019
- **Source**: scheduled-task-v2.backend.design.md §3.1.5、§3.2.4、§3.5.3、§3.5.6
- **Scenarios**: S-17, E-03, E-15, B-05, B-06, B-08
- **Spec-Refs**: worker/harness-worker, snapshot/harness-snapshot, persistence/harness-data, api/harness-api, test/harness-test
- **Paths**: apps/agent-worker/src/muad_agent_worker/application/**, apps/agent-worker/src/muad_agent_worker/worker/**, apps/agent-worker/src/muad_agent_worker/main.py, tests/agent_worker/test_scheduled_v2_async_resume.py
- **E2E**:
- **Skip-Tests**: false
- **Skip-Reason**:

### Tests

```json
[
  {
    "id": "related",
    "argv": [
      "uv",
      "run",
      "python",
      "-m",
      "pytest",
      "-q",
      "tests/agent_worker/test_scheduled_v2_async_resume.py",
      "tests/agent_worker/test_task_lease_epoch.py",
      "tests/agent_worker/test_runtime_operation_races.py"
    ],
    "timeout": 600,
    "parallel": false
  }
]
```

### Description

实现 Task ASYNC 的 pending_tool_calls 检查点、ready-check、持久消费和 PG 补扫，覆盖结果早到及任意崩溃窗口；唤醒同一 Execution，不新增 Run/Fire。

### Checklist

- [x] 提交前后使用稳定 call_id；回执丢失或 checkpoint 前崩溃后按调用身份找回 operation 并比 input_hash，不能创建第二个业务调用。
- [x] 进入 WAITING_EXTERNAL 前 ready-check：ready 直接继续；pending 才持久 wait_kind/operations[] 与 checkpoint 并释放 lease；ready-check 与等待之间结果到达由持久补扫兜底。
- [x] 同模型回合多 operation 按 call_id 独立 receipt_seq/consumed_event_id 聚合；乱序不能用全局水位跳过，全部 terminal 才继续，部分失败按冻结 policy。
- [x] 消费确认、任务事件、checkpoint 与状态 CAS 采用可重放事务边界；重复 event 不重复注入模型工具结果；HTTP 在行锁外。
- [x] 补扫器独立受监督、有界且跨租户公平；丢 hint、Redis 不可用、Worker 重启都能前进；External resume 不额外占 Schedule 槽或增加 attempt。
- [x] 覆盖结果早到、回执丢失、每个 checkpoint 窗口崩溃、重复/乱序/变参结果及取消/deadline 后迟到；终态只记 late，不恢复。

### Spec Delta

```json
[]
```

### Log

- [2026-11-15] created (draft)
- [2026-11-15T10:17:59.575Z] started tests=c2b2b6a9bc17
- [2026-11-15] 多 operation 的水位收敛到一处纯逻辑：新增 `application/agentic/async_results.py`（`advance_watermarks`/`all_settled`/`watermark_of`/`payload_from_watermarks`/`reconcile_operations`），恢复与补扫共用。`wait_payload_json.operations[]` 每项按 `call_id` 独立带 `status/receipt_seq/consumed_event_id`，没有全局水位；`LATE` 归入终态（`claimer.WAIT_OPERATION_TERMINAL` 改为从 `suspension.TERMINAL_OPERATION_VALUES` 派生，不再自持一份终态集合）。
- [2026-11-15] 进入等待前的就绪检查落在 `WorkerLoop._wait`：先在事务外读 Task-owned 收件箱（行锁内不做 HTTP），**全部终态**就不写等待、不释放租约，落一条 checkpoint 后由 `_supervise_task` 的段循环在同一租约里继续（attempt/resume/Fire/Run 都不动）；仍有未终态才用刚读到的水位落 `WAITING_EXTERNAL`。`WAITING_EXTERNAL` 的唯一写入方仍是 `_wait`（补扫只推进水位，不改状态）。检查之后才到的结果由持久补扫兜底。
- [2026-11-15] 持久补扫 `worker/async_sweep.py`（`AsyncResultSweeper`，由 `WorkerLoop.run_async_sweep_forever` 独立起跑、可缺席）：每拍有界 32 行、按 `update_time` 升序取并轮转（**读失败也轮转**，否则 Runtime 抖动时队头占位会饿死后面租户的行）；行锁内重读载荷再推进，两个并发补扫只推进一次；全终态后由 claim 按 `wait_kind` 续接（`resume_count`+1、`attempt` 不变、不新建 Fire/Run、续接不占新槽）。候选行在锁内已终态时只记 `ASYNC/RESULT_RECEIVED{late}`，绝不回到 QUEUED。
- [2026-11-15] 崩溃恢复：`load_resume_state` 以 `last_checkpoint_id` 为唯一依据（去掉 `last_resume_reason` 门控），按调用身份对账两窗口——回合收口前崩溃的调用用**同一 `call_id` 重提交**（Runtime 幂等 + `input_hash` 复检；换参数 → `TOOL_OPERATION_INPUT_MISMATCH`，确定性、不重试，也不开第二个业务调用），回合收口后 checkpoint 前崩溃的调用按 `call_id` 命中 operation 并把提交回执换成真实结果、不重提交；还有未终态调用时执行器直接回"补写等待"信封（`_pending_envelope`），**不跑模型**。事件→历史的折叠同时改成按回合分组（此前把所有 tool 消息堆在所有 assistant 之后，多轮恢复是非法历史）。
- [2026-11-15] 幂等与事务边界：结果按 `event_id` 幂等（`receipt_seq` 只增不减，迟到旧结果不推回水位）；tool 结果按 `call_id` **替换单条**消息（重复恢复不重复注入）；等待状态 + checkpoint + 事件同事务；补扫的读在锁外、写水位在行锁内。
- [2026-11-15] 共享文件改动（供并行合并核对）：`worker/service.py` 增 `operations` 构造参数、`_wait` 返回 bool、`_supervise_task` 拆成"段循环 + `_reload_task`"、新增 `run_async_sweep_forever`；`worker/agentic_executor.py` 增 `operations` 属性与 `TaskOperationError` 映射；`worker/assembly.py` 未改；`main.py` 只动 `_start_background`（把 `build_agentic_executor(...)` 提到变量、给 `WorkerLoop` 传 `operations=agentic.operations`、`loops` 增一项 `worker.async_sweep`），`include_router` 区块未碰。
- [2026-11-15] 测试：新增 `tests/agent_worker/test_scheduled_v2_async_resume.py`（14 例，真实 PG + 真实并发会话：同回合两条调用的独立水位与乱序、全部终态才续接、部分失败不丢已完成结果、等待前就绪就地续接（不写 WAITING、attempt/resume 不变）、检查后到达由补扫兜底、Runtime 读不到时保持等待、两个崩溃窗口、变参冲突、迟到只记 late、并发补扫只推进一次、有界 + 跨租户轮转）；`tests/agent_worker/test_task_lease_epoch.py` 迁到 v2 列与 `LeaseRenewal`（TASK-015 遗留的两处 v1 口径）；`tests/agent_worker/test_scheduled_v2_agentic_adapter.py` 的 API-30 替身补 `list_operations` 并在 resume 场景把结果标成就绪（共享契约断言未改，仍是 COMPLETED / 无工具重放 / 一次模型调用）；`docs/configuration-inventory.csv` 只刷新本任务改动文件的常量行（claimer/service/agentic_executor/run）。
- [2026-11-15] 已知遗留（不在本 TASK 范围，不伪称覆盖）：SYNC 工具在"回合收口前崩溃"窗口没有操作身份可对账，历史里仍是"声明了却没有结果"的 assistant 轮（与 TASK-019 之前一致，未编造结果）；补扫的指标未登记（`task_async_result_sweep_total` 归 TASK-032 的指标目录），当前只有结构化日志；`tests/architecture/test_capacity_webchat_worker_console_metrics.py`（`context_compaction_total` 的 `layer` label 不在低基数白名单）与 `tests/test_configuration_inventory.py`（`execution_outcomes.py`/`task_operation_client.py`/`checkpoints.py`/`main.py` 的陈旧行）在本任务开始前即为红。

### Verification

- 声明测试（3 个文件，30 条）已通过：`task finish` PASS（1/1 command）。
- 业务场景（S-17、E-03、E-15、B-05、B-06、B-08）：未在本 TASK 内做场景级 E2E，由需求级终验执行。

<a id="task-021"></a>
- 2026-11-15T10:49:47.686Z PASS commands=1/1 executed=1 deduped=0 elapsed=17206ms tests=c2b2b6a9bc17 head=66c280480ae2 dirty=1
  - related PASS exit=0
## TASK-021: 实现 Human 提问、回答、超时与双侧 CAS

- **Status**: done
- **Depends**: TASK-003, TASK-013, TASK-019
- **Source**: scheduled-task-v2.backend.design.md §3.2.6、§3.3.7、§3.3.8、API-10、§3.5.8
- **Scenarios**: S-06, S-09, S-10, E-07, E-08, E-15, B-05, B-08
- **Spec-Refs**: worker/harness-worker, snapshot/harness-snapshot, api/harness-api, auth/harness-auth, secret/harness-secret, test/harness-test
- **Paths**: apps/agent-worker/src/muad_agent_worker/application/**, apps/agent-worker/src/muad_agent_worker/api/**, apps/agent-worker/src/muad_agent_worker/worker/**, apps/agent-worker/src/muad_agent_worker/main.py, tests/agent_worker/test_scheduled_v2_human.py, tests/agent_worker/test_scheduled_v2_human_races.py
- **E2E**:
- **Skip-Tests**: false
- **Skip-Reason**:

### Tests

```json
[
  {
    "id": "related",
    "argv": [
      "uv",
      "run",
      "python",
      "-m",
      "pytest",
      "-q",
      "tests/agent_worker/test_scheduled_v2_human.py",
      "tests/agent_worker/test_scheduled_v2_human_races.py",
      "tests/agent_worker/test_task_deadline.py",
      "tests/agent_worker/test_worker_leases.py"
    ],
    "timeout": 600,
    "parallel": false
  }
]
```

### Description

将 Kernel 的 NEEDS_HUMAN_INPUT 持久化为任务中心唯一 OPEN interaction，并从同一检查点恢复；问题不属于 Conversation，不另建 Todo 权威。

### Checklist

- [x] 同事务写 checkpoint、OPEN interaction、WAITING_HUMAN 与释放 lease；每个 Execution 同时至多一个 OPEN，问题 deadline 不晚于 Execution。
- [x] 回答按 interaction_id 核对 tenant/user/agent、schema 与 response hash；双侧 CAS OPEN+WAITING_HUMAN+未取消+deadline 有效且旧 lease 已释放。
- [x] RESOLVED、回答、Execution QUEUED、lease_epoch/resume_count 与事件同事务；相同回答重放，变更回答冲突；resume 不增加 attempt。
- [x] 独立 timeout sweep 原子 TIMED_OUT 与 CANCELLED(HUMAN_TIMEOUT)；FUTURE_ONLY 保留 OPEN，ALL_ACTIVE 才关闭；已终态不得复活。
- [x] 真实 PG 并发验证回答/取消/超时各赢的路径、非法 schema 回滚、重复回答、失约 checkpoint、多个执行独立问题及 Prompt/Secret 输入。
- [x] 从 DB checkpoint 与原 Snapshot 恢复同一个 Execution；Human 提问投递失败不回滚等待，仍可在任务中心回答。

### Spec Delta

```json
[
  {
    "id": "DELTA-001",
    "op": "add",
    "target": "worker/harness-worker",
    "rule": "人工交互是 Execution 所属的持久待处理状态；问题、检查点、进入等待与释放执行权必须原子提交，问题有效期不得超出执行 deadline。回答须同时核对交互 OPEN、执行确在等待、取消标记与有效期，再原子保存回答并排队续接；相同回答幂等，变化冲突。仅停止未来调度时保留已开始执行与 OPEN 问题，终止全部或超时时才原子关闭；任何回答或迟到结果都不得复活终态。",
    "evidence": "scheduled-task-v2.backend.design.md §3.2.6、§3.4.5、§3.5.8；S-09/S-10/E-07/E-15 与 TASK-021。"
  }
]
```

### Log

- [2026-11-15] created (draft)
- [2026-11-15T10:18:03.109Z] started tests=93e502a57f94
- [2026-11-15] API-10：新增 `api/human_interactions.py`（`POST /internal/human-interactions/{id}/respond`，服务身份 + `X-Tenant-Id`/`X-Actor-User-Id`/可选 `X-Agent-Id` + `Idempotency-Key` 台账 `human-respond`）并在 `main.py` 注册路由。
- [2026-11-15] 回答实现 `application/human_interactions.py`：按 `interaction_id` 核对 tenant/owner/声明 Agent → `input_schema`（`jsonschema`）→ **双侧 CAS**（`interaction OPEN→RESOLVED` + `execution WAITING_HUMAN→QUEUED`，条件含 `cancel_requested=false`、`deadline_at > PG now`、`lease_owner/lease_until` 为空；同事务 `lease_epoch+1`、`resume_count+1`、`last_resume_reason=HUMAN` 与 `EXECUTION/RESUMED`+`HUMAN/RESOLVED` 事件）。终态执行/已取消/已超时一律拒绝，不复活。
- [2026-11-15] 幂等两层：`Idempotency-Key` 台账管同一次提交重放；interaction 级按规范化回答指纹——同内容重放首次结果（RESOLVED+QUEUED），不同内容 `IDEMPOTENCY_MISMATCH`。事件只落 `response_hash`，不落回答正文。
- [2026-11-15] 一次性续接标记：回答事务在 `wait_payload_json` 写 `{"human_interaction_id": …}`，claim 读到即按 Human 续接处理并清空（不再从 `WAITING_HUMAN` 直接 claim）；`resume_count` 只在回答事务计一次，`attempt` 不变。执行器按"`resolved_at` 晚于最近 checkpoint `create_time`"取回回答，以 USER 消息接在 checkpoint 历史之后（`agentic_executor._with_human_answer`），无可用回答时告警并保持原恢复状态。
- [2026-11-15] 修复 TASK-019 留下的真问题：`TIMER_STATUSES` 曾含 `WAITING_HUMAN` 且 `_wait_human` 把 `not_before` 写成问题 deadline —— 到期那一刻 claim 会把等待人工的执行直接起跑（等于凭空回答）。现等待人工没有任何定时唤醒源，只有回答改回 QUEUED 才续接；`not_before` 对 WAITING_HUMAN 不再参与领取。
- [2026-11-15] 超时：新增 `worker/human_timeouts.py`（`HumanTimeoutSweeper` 有界扫描 + 逐条事务；`HumanTimeoutLoop` 独立节拍，复用 `task_deadline_sweep_interval_sec` 作为唯一设置来源）。到期写成 **`CANCELLED` + `error_code=HUMAN_INTERACTION_EXPIRED` + `failure_class=HUMAN_TIMEOUT`** 并关 interaction `TIMED_OUT`；执行已终态时**只关 interaction**，不改写执行、不补执行事件。执行 CAS 只认 `status=WAITING_HUMAN AND cancel_requested=false`，与 deadline sweep 的 `NON_TERMINAL` 终态 CAS 互斥——先到者生效、后者整体回滚。
- [2026-11-15] 按设计修正（曾误写 FAILED）：`backend.design.md` §3.2.6/§3.5.8 与 scenarios S-06 三处写死超时为 `Execution CANCELLED(HUMAN_TIMEOUT)`，初版实现误取 `FAILED(HUMAN_TIMEOUT)`；现终态为 `CANCELLED`，`failure_class` 保留 `HUMAN_TIMEOUT` 记录原因，事件对为 `EXECUTION/CANCELLED` + `HUMAN/TIMED_OUT`，相关用例断言同步收紧（不再兼容两种状态）。
- [2026-11-15] `TerminalChange` 增加 `failure_class` 并在 `write_terminal` 唯一出口写入（超时是唯一使用方，其余终态保持 NULL）。
- [2026-11-15] `FUTURE_ONLY` 保留 OPEN 与 `WAITING_HUMAN`、`ALL_ACTIVE`/Execution 取消原子关闭 interaction：由 TASK-013 的矩阵实现，本 TASK 用真实接口回归并加断言。
- [2026-11-15] Checklist 第 1 条的实现来自 TASK-019 的 `WorkerLoop._wait_human`（同事务写 checkpoint + OPEN interaction + WAITING_HUMAN + 释放 lease，`deadline=min(问题, 执行)`，部分唯一索引保证每 Execution 至多一个 OPEN）；本 TASK 未改该函数，只补了与之配套的"等待人工不可被定时 claim"不变量并在测试中保留其回归。
- [2026-11-15] Checklist 第 6 条中"投递失败不回滚等待"是结构性事实：`_wait_human` 事务内没有任何投递写入（Human 推送是 TASK-022 的独立 `task_delivery`），因此投递失败无从回滚等待；本 TASK 验证的是从 DB checkpoint + 原 Snapshot 恢复同一个 Execution（回答以用户消息接续）。
- [2026-11-15] 测试：`tests/agent_worker/test_scheduled_v2_human.py`（18 条，真实路由 + 真实 PG）与 `tests/agent_worker/test_scheduled_v2_human_races.py`（6 条，并发方各用独立 Session/事务）。`test_worker_leases.py` 的 Human 分支按新契约改为"等待人工不可被 claim"。
- [2026-11-15] `docs/configuration-inventory.csv` 按 AST 重新盘点本 TASK 触及模块的常量。既有陈旧项（`execution_outcomes.py`/`task_operation_client.py`/`application/agentic/checkpoints.py` 行号漂移）在 main 上已红，非本 TASK 引入，未越界修改。

### Verification

- 尚未执行；此 TASK 的声明测试、业务场景均未宣称通过。

<a id="task-022"></a>
- 2026-11-15T10:30:59.002Z PASS commands=1/1 executed=1 deduped=0 elapsed=33160ms tests=89e024a199fb head=66c280480ae2 dirty=1 tests-changed
  - related PASS exit=0
- 2026-11-15T10:33:04.060Z PASS commands=1/1 executed=1 deduped=0 elapsed=34989ms tests=89e024a199fb head=f1d455a027a2 dirty=1 tests-changed
  - related PASS exit=0
## TASK-022: 拆分独立 Delivery 并可靠推送 WeCom

- **Status**: done
- **Depends**: TASK-003, TASK-008, TASK-013, TASK-016, TASK-021
- **Source**: scheduled-task-v2.backend.design.md §3.2.11、§3.3.10、§3.5.9
- **Scenarios**: S-01, S-02, S-06, S-07, S-10, S-11, E-09, E-11
- **Spec-Refs**: worker/harness-worker, im/harness-im, api/harness-api, secret/harness-secret, test/harness-test
- **Paths**: apps/agent-worker/src/muad_agent_worker/delivery/**, apps/agent-worker/src/muad_agent_worker/application/terminal_tasks.py, apps/im-gateway/src/muad_im_gateway/application/channel_delivery.py, apps/im-gateway/src/muad_im_gateway/api/delivery.py, tests/agent_worker/test_scheduled_v2_delivery.py, tests/agent_worker/test_delivery.py, tests/agent_worker/test_delivery_messages.py, tests/gateway/test_capacity_webchat_gateway_delivery.py
- **E2E**:
- **Skip-Tests**: false
- **Skip-Reason**:

### Tests

```json
[
  {
    "id": "related",
    "argv": [
      "uv",
      "run",
      "python",
      "-m",
      "pytest",
      "-q",
      "tests/agent_worker/test_scheduled_v2_delivery.py",
      "tests/agent_worker/test_delivery.py",
      "tests/agent_worker/test_delivery_messages.py",
      "tests/gateway/test_capacity_webchat_gateway_delivery.py"
    ],
    "timeout": 600,
    "parallel": false
  }
]
```

### Description

以 task_delivery/attempt 为唯一投递状态源，结果或 Human 问题先持久化后投递。NONE 完全不建投递/通知，PUSH 使用冻结创建路由。

### Checklist

- [x] 最终结果键 execution:{id}:final，Human 问题键 human:{interaction_id}；继承稳定键去重，调度域不再把旧 Task delivery 列当真相。
- [x] 预留 attempt/lease 先提交、HTTP 后发送；只有封套 data.delivered=true 才 SENT；2xx 缺确认、非法回执、超时按配置分类。
- [x] 保留 durable 退避、耗尽终态、旧 lease 不得确认；跨租户前进游标避免失败租户占满名额；进程崩溃后的预留可结算恢复。
- [x] PUSH 仅使用创建时 route/bot/conversation 元数据；route 失效明确 FAILED，不猜测替代会话；投递失败不改业务 Execution 终态。
- [x] WebChat 定时任务无 task_delivery、runtime.channel_notification 或聊天结果事件；立即任务原有 JOIN/DETACH 通知按其原契约运行。
- [x] 覆盖最终结果/Human 问题、发送前后崩溃、重复发送/ack、路由失效、NONE 以及两个租户公平性。

### Spec Delta

```json
[
  {
    "id": "DELTA-001",
    "op": "add",
    "target": "worker/harness-worker",
    "rule": "投递意图与业务执行状态分离：PUSH 的最终结果及人工问题先持久化独立投递记录，再以稳定业务键、持久尝试和有效发送租约驱动；只有接收方明确证明送达才能确认成功，失败不得改写业务执行终态。NONE 的定时结果与问题只由任务中心读取，禁止产生投递、会话消息或通知未读。投递采用创建时的明确目标，路由失效不得猜测其他会话，跨租户扫描和崩溃恢复必须有界且可前进。",
    "evidence": "scheduled-task-v2.backend.design.md §3.2.11、§3.3.10、§3.5.9；S-11/E-09/E-11 与 TASK-022。"
  }
]
```

### Log

- [2026-11-15] created (draft)
- [2026-11-15T10:55:02.772Z] started tests=cc3f305fa5ca
- [2026-11-15] `delivery/**` 以 `task_delivery`/`task_delivery_attempt` 为唯一状态源重做：不再读执行行已移除的投递列与 `intent_key`，扫描/候选/退避/上限全部改在投递表上（`delivery/service.py`、`delivery/client.py`、`delivery/messages.py`）；稳定键与种类收敛到新模块 `delivery/records.py`（`execution:{id}:final` / `human:{interaction_id}`），终态入队、立即任务创建与 Human 问题发现共用同一份拼法。
- [2026-11-15] 投递状态机：`PENDING` --预留(attempt_no+1 + `lease_owner/lease_until` + attempt 行 PENDING，先提交)--> HTTP --> 确认 `data.delivered=true` 且租约仍是本次 ⇒ `SENT` + attempt SENT + `DELIVERY/SENT` 事件；可重试失败（5xx/传输/超时/2xx 缺确认/非法回执）⇒ 留 `PENDING`（`update_time` 起算退避，attempt 行记 `FAILED` + 错误码）；4xx 或耗尽 `delivery_max_attempts` ⇒ `FAILED` + `DELIVERY/FAILED`；问题被回答/执行被清理 ⇒ `CANCELLED`。确认与失败都以 `(status=PENDING, lease_owner, lease_until)` 做 CAS，旧租约落败即 `delivery_lease_lost`，不写 SENT。
- [2026-11-15] 网关回执解析补上业务码（成功码 `"0"` 不算失败原因）：`last_error_code` 记 `BOT_NOT_FOUND`/`FORBIDDEN`/`HTTP_5xx`/`DELIVERY_UNCONFIRMED` 等，供投递记录与 Schedule 注意原因使用。
- [2026-11-15] 路由永久失效（bot 删除/入口解绑/路由停用）：投递终态 `FAILED`、不猜替代会话、不再重试；来源 Schedule 置 `NEEDS_ATTENTION` + `attention_reason`/`attention_since`、停未来 Fire 并写 `SCHEDULE/NEEDS_ATTENTION` 事件。**只改 Schedule 的调度状态**，Execution 业务终态不动；立即任务没有 Schedule 时只落投递记录。
- [2026-11-15] Human 问题投递：问题先由等待事务持久化（`_wait_human` 未改，保持「投递失败回滚不了等待」），投递循环按 `delivery_intent=PUSH` + 通道在 `OUTBOUND_CHANNELS`（共享词表，核心域不点名渠道）**有界发现**并补记 `HUMAN_QUESTION` 行，同键唯一约束保证重跑不新增；正文含任务名（Schedule 名称/提醒正文）、问题、deadline 与回复方式。
- [2026-11-15] 终态事务补 `DELIVERY/ENQUEUED` 事件（`application/terminal_tasks.py`）；立即任务创建同样经 `delivery.records` 写独立投递意图并记 ENQUEUED（`application/task_service.py`），不再双写执行行。
- [2026-11-15] 崩溃恢复：预留后崩溃、租约过期且预算已耗尽 ⇒ 结算为 `FAILED(reservation_abandoned)`；预算未尽 ⇒ 租约到期后另一个副本可接手（尝试次数继续累加）。
- [2026-11-15] 契约同步（`packages/contracts/src/muad_contracts/delivery.py`）：后台任务交付键改为 `execution:{task_id}:final`，新增 `human:{interaction_id}`（人工问题按 interaction 记账，`task_id` 只作上下文）；`task:` 旧形态随之移除，gateway/验收里构造请求的用点同步改名。
- [2026-11-15] 测试：`tests/agent_worker/test_delivery.py`（23 例，全部改种 `task_delivery`；含并发只发一次、重启保留退避、预留先提交、旧租约不得确认、过期租约可接手、产物形态、跨租户游标/单租户设置故障隔离、滞留预留结算）、`tests/agent_worker/test_delivery_messages.py`（6 例，最终结果与 Human 问题正文）、新增 `tests/agent_worker/test_scheduled_v2_delivery.py`（11 例，终态入队与 NONE 零投递、Human 问题推送/去重/已答作废、路由失效与 inactive 路由、崩溃后同键重投、两租户都被投递）。
- [2026-11-15] 遗留（非本 TASK 引入，均已确认为 v1 红）：`tests/agent_worker/test_capacity_webchat_worker_notifications.py` 仍按执行行投递列断言（本轮只把它的 `WEBCHAT_CHANNEL` 导入改到 owner 模块，避免收集期报错）；`tests/gateway/test_delivery_api.py::test_e05_worker_http_to_gateway_redis_probe_end_to_end` 仍用 `intent_key`；`test_admin_schedules.py`/`test_schedule_writes.py`/`test_task_queries.py`/`test_worker_metrics.py` 仍导入 v1 `ScheduleService`（收集期中断）。

### Verification

- 尚未执行；此 TASK 的声明测试、业务场景均未宣称通过。

<a id="task-023"></a>
- 2026-11-15T11:25:13.174Z PASS commands=1/1 executed=1 deduped=0 elapsed=23079ms tests=cc3f305fa5ca head=576edb624f6b dirty=1
  - related PASS exit=0
## TASK-023: 提供有界的 Worker Schedule/Fire/Execution 读面

- **Status**: done
- **Depends**: TASK-012, TASK-013, TASK-014, TASK-020, TASK-021, TASK-022
- **Source**: scheduled-task-v2.backend.design.md §3.3.12、API-07/08/09、§3.4.6
- **Scenarios**: S-02, S-03, S-07, S-09, S-12, E-02, E-10, B-01, B-02, B-09, B-10, B-11
- **Spec-Refs**: api/harness-api, auth/harness-auth, persistence/harness-data, secret/harness-secret, time/harness-time, test/harness-test
- **Paths**: apps/agent-worker/src/muad_agent_worker/api/**, apps/agent-worker/src/muad_agent_worker/application/**, tests/agent_worker/test_scheduled_v2_read_api.py
- **E2E**:
- **Skip-Tests**: false
- **Skip-Reason**:

### Tests

```json
[
  {
    "id": "related",
    "argv": [
      "uv",
      "run",
      "python",
      "-m",
      "pytest",
      "-q",
      "tests/agent_worker/test_scheduled_v2_read_api.py",
      "tests/agent_worker/test_task_queries.py",
      "tests/agent_worker/test_tenant_guard.py"
    ],
    "timeout": 600,
    "parallel": false
  }
]
```

### Description

交付统一的列表/详情/Fire-DST 历史/Execution/待处理/徽标与事件查询，让 Console 和 TaskCenter 读取同一实体，不复制结果库。

### Checklist

- [x] 查询核对 tenant+owner+授权 Agent 集合，显式 agent_id 必须在集合内；跨用户、跨租户、不再授权和软删一律不可见。
- [x] Schedule 详情含当前 Revision/配置 CAS/依赖摘要/预算/时区/投递意图；Fire 与 Execution 状态分别呈现（Delivery 状态由 `delivery_status` 单独读取——契约 `ExecutionDetail` 不含投递字段，Console 面归 TASK-029）。
- [x] Fire 历史真实 items 与 dst_events 分开；Execution 返回 wait_kind/脱敏等待载荷、开始/结束、结果、Snapshot 摘要与精确产物 version/checksum。
- [x] PendingPage 混合 HUMAN 与 NEEDS_ATTENTION 且统一分页；徽标与列表、回答使用同一 Agent 范围，不依 entry/channel 过滤。
- [x] 名称与最近 Fire/结果批量 JOIN/聚合，查询数有上界，稳定排序与分页不重复丢页；禁止 N+1、完整 Prompt/内部路径/Secret 外泄。
- [x] 验证真实查询计划/索引与高频历史，不以全表扫描获取结果；历史清理有显式提示，不能伪造空成功（**未完成**：`EXPLAIN` 级验证与高频历史的清理提示没有做，索引由 TASK-002 提供、当前只靠查询形状保证不扫全表）。

### Spec Delta

```json
[]
```

### Log

- [2026-11-15] 新增 `application/schedule_read.py`：列表/详情/Fire 历史/Execution/待处理/徽标的唯一投影，全部走契约 DTO（`ScheduleListItem`/`ScheduleDetail`/`FireHistoryPage`/`ExecutionDetail`/`PendingPage`/`TaskCenterBadges`）。
- [2026-11-15] 列表页固定条数批量查询：Schedule+Revision 一次 JOIN、最近 Fire 一次、OPEN 交互一次，不做 N+1；排序 `update_time DESC, id` 与 `scheduled_at DESC, id` 稳定。
- [2026-11-15] API-09 `GET /internal/executions/{id}` 按 tenant+actor 读取；等待载荷只在 `WAITING_EXTERNAL` 返回，其余状态（含 Human 续接后残留的 pending payload）一律 None。
- [2026-11-15] 新增 `/internal/task-center/pending` 与 `/badges`（API-16/17 的数据源），同一 owner+agent 范围，不按 entry/channel 过滤；`api/tasks.py` 的 `wait_payload` 同步按状态脱敏。
- [2026-11-15] 迁移认领的遗留测试：`test_task_queries.py`（直插 v2 Schedule+Revision、事件改用 `QUEUED`/`RECLAIMED`）、`test_tenant_guard.py`（写入口改为 v2 动作端点，接受态断言 `SCHEDULE_NOT_FOUND`）。
- [2026-11-15] created (draft)
- [2026-11-15T11:28:08.304Z] started tests=4bc6481e297b

### Verification

- 尚未执行；此 TASK 的声明测试、业务场景均未宣称通过。

<a id="task-024"></a>
- 2026-11-15T11:34:44.786Z PASS commands=1/1 executed=1 deduped=0 elapsed=13328ms tests=4bc6481e297b head=23519e4a7e53 dirty=1
  - related PASS exit=0
## TASK-024: 挂载 WebChat 任务中心与安全下载/确认路由

- **Status**: done
- **Depends**: TASK-008, TASK-012, TASK-013, TASK-021, TASK-023
- **Source**: scheduled-task-v2.backend.design.md §3.2.7、§3.4.1、API-12..19/25/31；scheduled-task-v2.frontend.design.md §3.9、§3.11
- **Scenarios**: S-02, S-07, S-09, S-11, S-12, E-11, E-14, B-09, B-11
- **Spec-Refs**: im/harness-im, api/harness-api, auth/harness-auth, secret/harness-secret, persistence/harness-data, test/harness-test
- **Paths**: apps/im-gateway/src/muad_im_gateway/api/**, apps/im-gateway/src/muad_im_gateway/application/**, apps/im-gateway/src/muad_im_gateway/infrastructure/**, apps/im-gateway/src/muad_im_gateway/main.py, tests/gateway/test_scheduled_v2_task_center.py, tests/gateway/test_scheduled_v2_artifact_download.py, tests/gateway/test_scheduled_v2_surface_contract.py
- **E2E**:
- **Skip-Tests**: false
- **Skip-Reason**:

### Tests

```json
[
  {
    "id": "related",
    "argv": [
      "uv",
      "run",
      "python",
      "-m",
      "pytest",
      "-q",
      "tests/gateway/test_scheduled_v2_task_center.py",
      "tests/gateway/test_scheduled_v2_artifact_download.py",
      "tests/gateway/test_scheduled_v2_surface_contract.py",
      "tests/architecture/test_channel_neutrality.py",
      "tests/architecture/test_im_gateway_boundaries.py"
    ],
    "timeout": 600,
    "parallel": false
  }
]
```

### Description

实现 surface-neutral TaskCenterService 和 WebChat API-12..19/25；API-31 代理真实用户确认到 Worker。MSSWWEB 只用 Fake principal 验证同形契约，本需求不挂路由或页面。

### Checklist

- [x] 沿用现有 ChatPrincipal/会话认证与 CSRF，服务端核属 user+authorized agents；entry/channel 只作元数据，跨入口允许同 user+agent 查询。
- [x] 列表/详情/徽标/pending/操作/回答共享同一范围与封套；写请求保留 Idempotency-Key 与可信 actor，外部 BFF 不得直连 internal。
- [x] 下载按 artifact.task_id→Execution 核对 tenant/actor/agent，要求精确 version+checksum，流式返回字节及 Content-Disposition，不要求 Conversation。
- [x] 跨用户/Agent、已删、内部 transcript、无权资源不可下载；版本冲突显式错误，不能返最新版；底层失败不能伪造成功。
- [x] WebChat/Fake surface 对同一输入输出做契约 parity，断言无 MSSWWEB task-center 路由、无渠道枚举流入核心域。
- [x] 针对定时任务验证读/答/执行全过程不产生 channel_notification、聊天结果或 Delivery；保留现有立即任务通知路径。

### Spec Delta

```json
[
  {
    "id": "DELTA-001",
    "op": "add",
    "target": "im/harness-im",
    "rule": "任务中心的服务与响应契约只依赖可信 Principal 和授权 Agent 范围，数据归属为租户、平台用户与 Agent，入口及渠道仅作来源元数据。不同 surface 复用同一读写及分页契约；定时结果和人工待处理由任务实体直接投影，不写会话通知。任务产物按执行 owner 核属并读取请求指定的不可变版本与校验值，不要求会话身份；未授权、软删或内部留档不得暴露。",
    "evidence": "scheduled-task-v2.backend.design.md §3.2.7、§3.4.6；S-11/S-12/B-11，TASK-023/TASK-024。"
  }
]
```

### Log

- [2026-11-15] created (draft)
- [2026-11-15T11:35:08.031Z] started tests=4c2395eccc2a
- [2026-11-15] 实现 surface 中立 `TaskCenterService`（`application/task_center.py`）与按 ChatSurface 挂载的路由工厂（`api/task_center.py`）：WebChat 挂 10 条路由（API-12..19/25/31），MSSWWEB 只存在于契约测试。读面/动作/确认经新的 `WorkerClient`（服务身份 + 部署租户 + 已验证 actor），产物版本经 `RuntimeClient.resolve_task_artifact_version` 解析后从共享存储取件。
- [2026-11-15] 授权口径：`agent_id` 只与本次 Console 核验的授权集合取交集；列表按 Worker 稳定顺序读完整段后统一分页（`mode` 过滤后 `total` 仍准确、不泄露撤销 Agent 的条数）；回答在授权集合为空时 403；Execution 详情与 Draft 确认按 Worker 的 `tenant+actor` 核属（契约不携带 `agent_id`，已在模块头与报告中注明）。
- [2026-11-15] 写面：动作/回答/确认都必须带 `Idempotency-Key`；确认路由把它作为 `confirmation_ref` 并写 `X-Confirmation-Surface=WEBCHAT`（模型工具路径到不了这条路由）。动作后重读详情返回生效事实，准入拒绝显式 422。
- [2026-11-15] 复用取舍：WebChat 附件与任务产物共用一个取件判定（`webchat_artifacts.open_download_reference`），`api/webchat._download_response` 提升为 `download_response` 供两条下载路由共用（日志事件名改为 `artifact_download_*`）。
- [2026-11-15] 测试：`tests/gateway/test_scheduled_v2_task_center.py`（16 例，真实 Console/Worker HTTP 桩）、`test_scheduled_v2_artifact_download.py`（9 例，真实 Runtime 版本解析 + 真实共享存储）、`test_scheduled_v2_surface_contract.py`（5 例：WebChat/Fake MSSWWEB 同形 data、同范围、确认来源、handler 集合一致、生产 app 无 MSSWWEB 路由）；`docs/configuration-inventory.csv` 按 AST 重新盘点（新增两模块常量与既有模块行号漂移）。

### Verification

- 尚未执行；此 TASK 的声明测试、业务场景均未宣称通过。

<a id="task-025"></a>
- 2026-11-15T11:54:08.220Z PASS commands=1/1 executed=1 deduped=0 elapsed=31273ms tests=4c2395eccc2a head=bea3a1bd4687 dirty=1
  - related PASS exit=0
## TASK-025: 接通 WeCom 用户确认与 Human 回复分流

- **Status**: draft
- **Depends**: TASK-013, TASK-021, TASK-022, TASK-017
- **Source**: scheduled-task-v2.backend.design.md §3.2.6、§3.2.8、§3.5.8、§3.5.9
- **Scenarios**: S-01, S-06, S-10, E-08, E-09, E-14, E-15
- **Spec-Refs**: im/harness-im, auth/harness-auth, api/harness-api, worker/harness-worker, secret/harness-secret, test/harness-test
- **Paths**: apps/im-gateway/src/muad_im_gateway/application/inbound*, apps/im-gateway/src/muad_im_gateway/channels/**, apps/im-connector/src/muad_im_connector/channels/wecom/**, apps/agent-runtime/src/muad_agent_runtime/application/**, packages/contracts/src/muad_contracts/**, tests/gateway/test_scheduled_v2_wecom_flow.py, tests/agent_runtime/test_scheduled_v2_human_routing.py
- **E2E**:
- **Skip-Tests**: false
- **Skip-Reason**:

### Tests

```json
[
  {
    "id": "related",
    "argv": [
      "uv",
      "run",
      "python",
      "-m",
      "pytest",
      "-q",
      "tests/gateway/test_scheduled_v2_wecom_flow.py",
      "tests/agent_runtime/test_scheduled_v2_human_routing.py",
      "tests/architecture/test_channel_neutrality.py",
      "tests/architecture/test_im_gateway_boundaries.py"
    ],
    "timeout": 600,
    "parallel": false
  }
]
```

### Description

把真实 WeCom 按钮/明确回复转为可信 Draft 确认或特定 Human 回答；通道解析仅在适配器，核心只接收中立用户动作与 origin 元数据。

### Checklist

- [ ] 确认依据真实入站引用与已认证 user/agent，携带稳定 surface/ref 到确认端点；重放不重复生效，模型文本不能伪造确认。
- [ ] Schedule 创建路由保存 origin_bot_account_id 与原会话，只向该目标推送最终结果/问题，解绑时明确失败。
- [ ] Human 匹配范围 user+agent+创建会话：恰好一个 OPEN 才回答，多个列出任务要求指定，零个继续普通聊天。
- [ ] 回答通过 Worker 核属与双侧 CAS；同源消息重投幂等；取消/超时后不恢复，任务中心仍读取同一交互状态。
- [ ] 适配器核验的回复不新建无意义 Run；测试两个会话、多个问题、错误用户/Agent、route 失效和原始通道字段不越界。

### Spec Delta

```json
[]
```

### Log

- [2026-11-15] created (draft)

### Verification

- 尚未执行；此 TASK 的声明测试、业务场景均未宣称通过。

<a id="task-026"></a>

## TASK-026: 接通 WebChat typed client、保留对话状态的 Tab 壳与 Draft 卡片

- **Status**: draft
- **Depends**: TASK-024, TASK-017
- **Source**: scheduled-task-v2.frontend.design.md §3.2、§3.3、§3.5、§3.9、§3.12；scheduled-task-v2.backend.design.md §3.2.8、API-31
- **Scenarios**: S-01, S-02, S-03, S-04, S-09, S-11, S-14, E-01, E-04, E-05, E-14
- **Spec-Refs**: frontend/harness-frontend, im/harness-im, i18n/harness-i18n, ui/harness-ui, api/harness-api, secret/harness-secret, test/harness-test
- **Paths**: apps/console-platform/frontend/src/App.tsx, apps/console-platform/frontend/src/modules/webchat/**, apps/console-platform/frontend/src/i18n/**, tests/frontend/test_scheduled_v2_webchat_client.py, tests/frontend/test_scheduled_v2_workspace.py, tests/frontend/test_scheduled_v2_draft_card.py
- **E2E**:
- **Skip-Tests**: false
- **Skip-Reason**:

### Tests

```json
[
  {
    "id": "related",
    "argv": [
      "uv",
      "run",
      "python",
      "-m",
      "pytest",
      "-q",
      "tests/frontend/test_scheduled_v2_webchat_client.py",
      "tests/frontend/test_scheduled_v2_workspace.py",
      "tests/frontend/test_scheduled_v2_draft_card.py",
      "tests/frontend/test_capacity_webchat_chat_auth_state.py",
      "tests/frontend/test_api_client_contract.py"
    ],
    "timeout": 600,
    "parallel": false
  },
  {
    "id": "typecheck",
    "argv": [
      "npm",
      "--prefix",
      "apps/console-platform/frontend",
      "run",
      "typecheck"
    ],
    "timeout": 180,
    "parallel": true
  }
]
```

### Description

通过既有 CHAT client/surface 接入 TaskCenter typed service；静态路由与 Tab 壳持有同一对话控制器，聊天里的专用 Draft 卡直接调确认 API，不走 Run resume。

### Checklist

- [ ] service 唯一 HTTP 出口，复用 src/api/client 拦截器注入请求头/CSRF/CHAT 上下文，写操作加 Idempotency-Key；组件/hook 不裸 axios/fetch。
- [ ] 静态 /chat/tasks 和 /chat/tasks/:scheduleId 正确优先于 entryId；切 Tab 保留流式 Run、草稿、entry/conversation 返回参数，不卸载 useRunEvents。
- [ ] schedule.draft.ready 的当前流/历史都渲染预览卡，确认按钮只调 surface API-31；禁用重复点击，同一动作重试保留幂等键。
- [ ] 清楚显示做什么、何时、IANA 时区、结果去哪看、allowlist/budget/READ_ONLY 及 UPDATE 差异；过期/冲突/配额有明确反馈。
- [ ] 身份切换/退出/Agent 切换中止旧请求、撤 Blob URL、丢弃旧 generation 响应，401 不自动重发写请求。
- [ ] 测试真实 TS 构建后的 client 与状态逻辑，组件用仓库现有 component 设施；运行 typecheck，验证 active stream 跨 Tab 不丢。

### Spec Delta

```json
[
  {
    "id": "DELTA-001",
    "op": "add",
    "target": "frontend/harness-frontend",
    "rule": "聊天工作区切换到任务中心时必须保留当前对话控制器及流式执行，任务中心请求以身份与查询 generation 隔离，旧响应不得覆盖新视图。调度 Draft 使用独立的持久确认事件与 surface 确认动作，不能复用 Run interrupt/resume。任务中心只主动读取列表、详情与待处理，隐藏时停止刷新；所有 HTTP 与认证仍走共享客户端和模块服务层，成功操作后刷新服务端真相，不伪造最终状态。",
    "evidence": "scheduled-task-v2.frontend.design.md §3.2、§3.5、§3.9；TASK-026/TASK-027/TASK-028。"
  }
]
```

### Log

- [2026-11-15] created (draft)

### Verification

- 尚未执行；此 TASK 的声明测试、业务场景均未宣称通过。

<a id="task-027"></a>

## TASK-027: 展示任务列表、详情、Fire 历史与版本产物

- **Status**: draft
- **Depends**: TASK-026
- **Source**: scheduled-task-v2.frontend.design.md §3.3、§3.4、§3.5、§3.6、§3.7、§3.8
- **Scenarios**: S-02, S-03, S-04, S-07, S-11, S-15, S-16, E-02, E-10, E-11, B-01, B-02, B-03, B-09, B-10, B-11
- **Spec-Refs**: frontend/harness-frontend, ui/harness-ui, ui-detail/harness-ui-detail, time/harness-time, i18n/harness-i18n, secret/harness-secret, test/harness-test
- **Paths**: apps/console-platform/frontend/src/modules/webchat/task-center/**, apps/console-platform/frontend/src/modules/webchat/task-center.css, apps/console-platform/frontend/src/modules/webchat/types.ts, apps/console-platform/frontend/src/i18n/**, tests/frontend/test_scheduled_v2_task_center_ui.py, tests/frontend/test_scheduled_v2_history_artifacts_ui.py
- **E2E**:
- **Skip-Tests**: false
- **Skip-Reason**:

### Tests

```json
[
  {
    "id": "related",
    "argv": [
      "uv",
      "run",
      "python",
      "-m",
      "pytest",
      "-q",
      "tests/frontend/test_scheduled_v2_task_center_ui.py",
      "tests/frontend/test_scheduled_v2_history_artifacts_ui.py",
      "tests/frontend/test_datetime_contract.py",
      "tests/frontend/test_common_tips_contract.py"
    ],
    "timeout": 600,
    "parallel": false
  },
  {
    "id": "typecheck",
    "argv": [
      "npm",
      "--prefix",
      "apps/console-platform/frontend",
      "run",
      "typecheck"
    ],
    "timeout": 180,
    "parallel": true
  }
]
```

### Description

交付 /chat/tasks 及详情深链：授权 Agent 过滤、独立分页、模式/状态/最近结果、Revision、Fire/DST 历史和按需 Execution 结果/产物。页面复用 Semi 与既有时间/详情组件。

### Checklist

- [ ] 列表/详情 controller 单一状态源，过滤默认全部授权 Agent；列表、徽标与待处理范围一致；加载、空态、404、失败重试均真实。
- [ ] Fire、Execution、Delivery 状态分别展示；missed_count 额外次数、结束原因与三类 wait_kind 不混为失败；DST 用独立事件展示。
- [ ] 长结果折叠/滚动，列表不塞全文；产物用 TaskArtifactList，version+checksum 下载指定版本，失败明确、Blob URL 随身份/组件释放。
- [ ] 详情显示冻结 Snapshot 摘要、allowlist、budget、READ_ONLY，不展示 Prompt、Secret 或内部 endpoint/路径；未知状态码有可读兜底。
- [ ] 仅 Tab 可见且用户触发时读取；过期响应按 epoch 丢弃，刷新失败保留有效旧内容；切走不后台轮询，不接定时结果 SSE。
- [ ] DateTimeText、catalog i18n、窄屏单列/返回、键盘 Tab/行聚焦、aria 标签完整；组件测试验证多页与产物错误，不只做源码文本匹配。

### Spec Delta

```json
[]
```

### Log

- [2026-11-15] created (draft)

### Verification

- 尚未执行；此 TASK 的声明测试、业务场景均未宣称通过。

<a id="task-028"></a>

## TASK-028: 展示待处理 Human 并执行生命周期动作

- **Status**: draft
- **Depends**: TASK-021, TASK-026, TASK-027
- **Source**: scheduled-task-v2.frontend.design.md §3.3、§3.4、§3.5、§3.6、§3.8；scheduled-task-v2.backend.design.md §3.4.5、§3.4.6
- **Scenarios**: S-04, S-05, S-06, S-09, S-10, E-05, E-06, E-07, E-08, E-13, E-15, B-04, B-12
- **Spec-Refs**: frontend/harness-frontend, ui/harness-ui, ui-detail/harness-ui-detail, i18n/harness-i18n, api/harness-api, time/harness-time, test/harness-test
- **Paths**: apps/console-platform/frontend/src/modules/webchat/task-center/**, apps/console-platform/frontend/src/i18n/**, tests/frontend/test_scheduled_v2_pending_ui.py, tests/frontend/test_scheduled_v2_actions_ui.py
- **E2E**:
- **Skip-Tests**: false
- **Skip-Reason**:

### Tests

```json
[
  {
    "id": "related",
    "argv": [
      "uv",
      "run",
      "python",
      "-m",
      "pytest",
      "-q",
      "tests/frontend/test_scheduled_v2_pending_ui.py",
      "tests/frontend/test_scheduled_v2_actions_ui.py",
      "tests/frontend/test_datetime_contract.py"
    ],
    "timeout": 600,
    "parallel": false
  },
  {
    "id": "typecheck",
    "argv": [
      "npm",
      "--prefix",
      "apps/console-platform/frontend",
      "run",
      "typecheck"
    ],
    "timeout": 180,
    "parallel": true
  }
]
```

### Description

待处理是任务中心的过滤视图：Human 与 NEEDS_ATTENTION 分类显示，回答特定 interaction 后刷新服务端数据；暂停/恢复/取消按真实矩阵反馈。

### Checklist

- [ ] PendingPage 独立分页与 Badge；按 interaction_id 显示任务、问题、deadline 和 schema 表单，字段错误关联输入；不在会话流展示问题。
- [ ] 同一次提交保留幂等键，响应成功刷新 pending+badges+当前详情；超时/已取消/版本冲突保留当前内容并提示刷新，不乐观伪造成功。
- [ ] 取消弹窗明确 FUTURE_ONLY/ALL_ACTIVE，默认停未来；展示保留进行中/待处理或终止全部，不能只传旧 cancel_running。
- [ ] 修改预填对话“修改任务 X：…”走新 Draft/确认；ENDED ONCE 只读并提供重新创建，不提供修改/恢复原 occurrence。
- [ ] NEEDS_ATTENTION 展示原因/修复动作/恢复入口，不生成 Human 表单；撤权后统一不可见，旧身份请求无法带回旧问题。
- [ ] 组件行为验证多 OPEN、回答失败重试、并发取消/超时的 UI、二次确认、键盘焦点及窄屏；不新增 Todo 或通知中心。

### Spec Delta

```json
[]
```

### Log

- [2026-11-15] created (draft)

### Verification

- 尚未执行；此 TASK 的声明测试、业务场景均未宣称通过。

<a id="task-029"></a>

## TASK-029: 适配 Console Schedule/Task API 与运营动作

- **Status**: draft
- **Depends**: TASK-013, TASK-023
- **Source**: scheduled-task-v2.backend.design.md §3.4.1、API-20..24、§3.4.6、§3.4.8；scheduled-task-v2.frontend.design.md §3.10
- **Scenarios**: S-07, S-13, E-12, E-13, B-01, B-10
- **Spec-Refs**: api/harness-api, auth/harness-auth, persistence/harness-data, time/harness-time, secret/harness-secret, test/harness-test
- **Paths**: apps/console-platform/backend/src/muad_console_platform/api/schedules.py, apps/console-platform/backend/src/muad_console_platform/api/tasks.py, apps/console-platform/backend/src/muad_console_platform/application/**, apps/console-platform/backend/src/muad_console_platform/infrastructure/repositories/**, tests/console_tasks/test_scheduled_v2_console_api.py, tests/console_tasks/test_schedules_api.py
- **E2E**:
- **Skip-Tests**: false
- **Skip-Reason**:

### Tests

```json
[
  {
    "id": "related",
    "argv": [
      "uv",
      "run",
      "python",
      "-m",
      "pytest",
      "-q",
      "tests/console_tasks/test_scheduled_v2_console_api.py",
      "tests/console_tasks/test_schedules_api.py"
    ],
    "timeout": 600,
    "parallel": false
  }
]
```

### Description

Console 继续提供同租户查看与暂停/恢复/取消，增加 Fire 历史和新执行摘要；权限沿用当前行为，没有创建或编辑用户意图入口。

### Checklist

- [ ] 列表/详情补 mode/status/end_reason/config_version/Revision/依赖/投递/预算、wait_kind/Snapshot/Human 摘要，统一来自 Worker 真相。
- [ ] API-22 分页返回 Fire+独立 DST 事件；API-23 单一路由 POST actions 调共用生命周期服务，移除旧 PUT pause/resume。
- [ ] 不新增 require_admin 或改变 Builder 权限；不提供 POST schedules/用户意图编辑；原 tenant 核属与不可见 404 保持。
- [ ] Console 时间继续 format_console_time 带 offset，内部保持 ISO；批量名称/聚合避免 N+1，不暴露 Secret 或内部存储路径。
- [ ] 回归任务列表/详情/取消与既有深链、统一封套/错误 catalog、跨租户、无创建能力及旧动作路由已移除。

### Spec Delta

```json
[]
```

### Log

- [2026-11-15] created (draft)

### Verification

- 尚未执行；此 TASK 的声明测试、业务场景均未宣称通过。

<a id="task-030"></a>

## TASK-030: 更新 Console Schedule/Task 现有页面

- **Status**: draft
- **Depends**: TASK-029
- **Source**: scheduled-task-v2.frontend.design.md §3.10、§3.12
- **Scenarios**: S-07, S-13, E-02, E-09, E-10, E-12, E-13, B-01, B-02, B-03, B-10
- **Spec-Refs**: frontend/harness-frontend, ui/harness-ui, ui-detail/harness-ui-detail, time/harness-time, i18n/harness-i18n, test/harness-test
- **Paths**: apps/console-platform/frontend/src/modules/task-schedule/**, apps/console-platform/frontend/src/i18n/**, tests/frontend/test_task_schedule_services.py, tests/frontend/test_task_schedule_i18n.py, tests/frontend/test_task_schedule_deep_link_contract.py, tests/frontend/test_scheduled_v2_console_ui.py
- **E2E**:
- **Skip-Tests**: false
- **Skip-Reason**:

### Tests

```json
[
  {
    "id": "related",
    "argv": [
      "uv",
      "run",
      "python",
      "-m",
      "pytest",
      "-q",
      "tests/frontend/test_scheduled_v2_console_ui.py",
      "tests/frontend/test_task_schedule_services.py",
      "tests/frontend/test_task_schedule_i18n.py",
      "tests/frontend/test_task_schedule_deep_link_contract.py",
      "tests/frontend/test_datetime_contract.py"
    ],
    "timeout": 600,
    "parallel": false
  },
  {
    "id": "typecheck",
    "argv": [
      "npm",
      "--prefix",
      "apps/console-platform/frontend",
      "run",
      "typecheck"
    ],
    "timeout": 180,
    "parallel": true
  }
]
```

### Description

仅改现有 /schedules 与 /tasks 页面，展示新状态/模式、Revision、依赖、Fire 历史与等待类型；服务层统一 actions，沿用详情侧栏和深链。

### Checklist

- [ ] Schedule 新状态与 end_reason、task_type、Agentic budget/READ_ONLY、Fire/DST 分页、Execution 链接全部映射 catalog。
- [ ] Task 详情展示 WAITING_EXTERNAL/WAITING_HUMAN 及各 wait_kind、Snapshot/Human 摘要；移除 FAILED_CONFIG 等旧列和旧状态断言。
- [ ] 操作使用 POST actions 与取消两档，实际成功后刷新；无创建按钮/表单，不增加 ADMIN-only 或全量结构化编辑器。
- [ ] 保留 ?scheduleId= 深链、取消/暂停/恢复现有 testid、404 与用户可读错误；DateTimeText 处理 offset，i18n 不硬编码业务文案。
- [ ] 组件行为与 typed service 回归覆盖 Fire 状态分离、空错态、分页、权限现状和未知码；运行 typecheck。

### Spec Delta

```json
[]
```

### Log

- [2026-11-15] created (draft)

### Verification

- 尚未执行；此 TASK 的声明测试、业务场景均未宣称通过。

<a id="task-031"></a>

## TASK-031: 实现有界保留清理并保护等待中的执行

- **Status**: draft
- **Depends**: TASK-008, TASK-014, TASK-020, TASK-021, TASK-022, TASK-023
- **Source**: scheduled-task-v2.backend.design.md §3.3.7、§3.5.7、§3.5.11；scheduled-task-v2.frontend.design.md §3.6
- **Scenarios**: S-07, S-09, S-17, B-09, B-10, B-11
- **Spec-Refs**: persistence/harness-data, worker/harness-worker, snapshot/harness-snapshot, secret/harness-secret, test/harness-test
- **Paths**: apps/agent-worker/src/muad_agent_worker/application/**, apps/agent-worker/src/muad_agent_worker/main.py, apps/agent-runtime/src/muad_agent_runtime/application/**, apps/console-platform/backend/src/muad_console_platform/application/artifact_cleanup_service.py, tests/agent_worker/test_scheduled_v2_retention.py, tests/agent_runtime/test_scheduled_v2_artifact_retention.py
- **E2E**:
- **Skip-Tests**: false
- **Skip-Reason**:

### Tests

```json
[
  {
    "id": "related",
    "argv": [
      "uv",
      "run",
      "python",
      "-m",
      "pytest",
      "-q",
      "tests/agent_worker/test_scheduled_v2_retention.py",
      "tests/agent_runtime/test_scheduled_v2_artifact_retention.py"
    ],
    "timeout": 600,
    "parallel": false
  }
]
```

### Description

按设置有界清理终态 Fire/Event/Execution/Snapshot/Checkpoint 与任务产物；等待 Human/ASYNC 或有效 lease 的执行继续保留可恢复材料，清理不抢占业务 owner。

### Checklist

- [ ] 清理边界取当前保留设置，有界批次/前进游标/租户隔离，锁外处理网络或文件；不在用户 GET 中扫描清理。
- [ ] 按真实 FK/owner 顺序清除可清理子项与结果发件，保留仍需重投/审计的引用；不破坏 workspace 或仍有效的 Run artifact。
- [ ] Snapshot/Checkpoint 随 Execution 保留期，不在执行完成时立即删；WAITING_HUMAN/EXTERNAL 永不提前失去恢复材料。
- [ ] Task artifact 清理由 Runtime owner 实施，文件与元数据失败显式记录并可重试，Worker 不跨 schema 直写。
- [ ] 查询对清理后的历史给出明确保留提示，保留期内分页不丢失；测试截止边界、并发回答/清理、终态等待投递及跨用户下载不可见。

### Spec Delta

```json
[]
```

### Log

- [2026-11-15] 追加硬性验收点（TASK-018 门禁期间发现）：`artifact_retention_repository.py` 仍只把 `'WAITING'` 当作活动执行，v2 的 `WAITING_EXTERNAL/WAITING_HUMAN` 会被当成可清理——即**等待中执行的产物会被误删**。保留清理必须把三种等待态都视为受保护，并有用例覆盖（否则该事实无人防守）。
- [2026-11-15] created (draft)

### Verification

- 尚未执行；此 TASK 的声明测试、业务场景均未宣称通过。

<a id="task-032"></a>

## TASK-032: 登记指标、监督循环并补齐部署回滚操作

- **Status**: draft
- **Depends**: TASK-005, TASK-014, TASK-020, TASK-021, TASK-022, TASK-024, TASK-031
- **Source**: scheduled-task-v2.backend.design.md §3.5.12、§3.5.13、§4.1、§4.2、§4.3、§4.4
- **Scenarios**: S-07, S-08, S-17, E-02, E-07, E-09, E-10, E-13, E-15, B-04, B-08, B-10
- **Spec-Refs**: arch/harness-arch, logging/harness-log, secret/harness-secret, worker/harness-worker, test/harness-test
- **Paths**: apps/agent-worker/src/muad_agent_worker/metrics.py, apps/agent-worker/src/muad_agent_worker/main.py, apps/agent-runtime/src/muad_agent_runtime/**, deploy/**, docs/operations/**, .code-flow/tasks/2026-11-15/scheduled-task-v2/*.design.md, tests/agent_worker/test_scheduled_v2_observability.py, tests/architecture/test_scheduled_v2_lifecycle_wiring.py
- **E2E**:
- **Skip-Tests**: false
- **Skip-Reason**:

### Tests

```json
[
  {
    "id": "related",
    "argv": [
      "uv",
      "run",
      "python",
      "-m",
      "pytest",
      "-q",
      "tests/agent_worker/test_scheduled_v2_observability.py",
      "tests/architecture/test_scheduled_v2_lifecycle_wiring.py",
      "tests/agent_worker/test_worker_metrics.py",
      "tests/architecture/test_import_direction.py"
    ],
    "timeout": 600,
    "parallel": false
  }
]
```

### Description

让新调度执行面可以观测、启动、关闭和恢复；操作文档描述真正的阶段门槛、停旧服务/排空在途、备份重建和整体版本切换，不交付第二套运行旁路。

### Checklist

- [ ] 指标按 counter/gauge/histogram 登记 CATALOG，覆盖 due、duplicate、skip、admission、等待/超时、Snapshot、ASYNC、artifact、delivery 及压缩降级。
- [ ] Trace/事件可从 Schedule→Revision→Fire→Execution→Human/Operation/Artifact/Delivery 追溯；labels 不含原始指令、敏感参数或高基数 Secret。
- [ ] due scan、deadline、Human timeout、External ready、Delivery、retention 独立受监督；慢/失败单循环不推迟其它 loop，shutdown 取消并等待后台任务。
- [ ] 部署配置按当前 Settings/ConfigMap 所有者注入，核对内部服务身份、Runtime API、Worker 槽位与产物可达；不硬编码端口/IP。
- [ ] 发布先停 dev/旧服务并查残留，备份、排空旧 Run/Task/job、迁移、统一切换消费者；破坏性 schema 回滚明确备份+对应镜像，不将旧镜像直接接新 schema。
- [ ] 按最终代码修正设计内旧 Kernel 抽取措辞、旧 WAITING 历史映射、missed_count 术语及接口事实；不修改 MSSWWEB BFF 接入文档、不引入兼容实现。
- [ ] 测试实际装配/loop 故障监督/指标样本/Secret 脱敏与关闭回收；确认特定问题“单跑绿整跑红”先查残留和隔离再复跑。

### Spec Delta

```json
[]
```

### Log

- [2026-11-15] created (draft)

### Verification

- 尚未执行；此 TASK 的声明测试、业务场景均未宣称通过。

<a id="task-033"></a>

## TASK-033: 补齐真实全链路旅程及 CI 测试接线

- **Status**: draft
- **Depends**: TASK-020, TASK-022, TASK-025, TASK-028, TASK-030, TASK-031, TASK-032
- **Source**: scheduled-task-v2.backend.design.md §3.5.14、§6；scheduled-task-v2.frontend.design.md §2.4、§3.10；scheduled-task-v2.scenarios.md S-01..S-17/E-01..E-15/B-01..B-12
- **Scenarios**: S-01, S-02, S-03, S-04, S-05, S-06, S-07, S-08, S-09, S-10, S-11, S-12, S-13, S-14, S-15, S-16, S-17, E-01, E-02, E-03, E-04, E-05, E-06, E-07, E-08, E-09, E-10, E-11, E-12, E-13, E-14, E-15, B-01, B-02, B-03, B-04, B-05, B-06, B-07, B-08, B-09, B-10, B-11, B-12
- **Spec-Refs**: e2e/harness-e2e, test/harness-test, im/harness-im, auth/harness-auth, secret/harness-secret
- **Paths**: e2e/playwright.scheduled-task-v2.config.ts, e2e/tests/scheduled-task-v2/**, e2e/tests/task-schedule/**, e2e/tests/webchat/**, e2e/scenarios/**, tests/acceptance/task_schedule/**, tests/e2e/**, tests/frontend/test_scheduled_v2_e2e_wiring.py, tests/frontend/test_e2e_suite_contract.py, .github/workflows/check.yml, Makefile, scripts/e2e_changed.py
- **E2E**: ci:e2e
- **Skip-Tests**: false
- **Skip-Reason**:

### Tests

```json
[
  {
    "id": "related",
    "argv": [
      "uv",
      "run",
      "python",
      "-m",
      "pytest",
      "-q",
      "tests/frontend/test_scheduled_v2_e2e_wiring.py",
      "tests/frontend/test_e2e_suite_contract.py",
      "tests/test_collection_layout.py"
    ],
    "timeout": 600,
    "parallel": false
  }
]
```

### Description

编写真实跨服务与浏览器旅程并接入现有 CI 分层；本 TASK finish 只执行下列相关 unit/contract 接线检查，真实 acceptance/E2E 结果由 CI 或人工独立执行记录，不把静态接线通过当作业务验收通过。

### Checklist

- [ ] 新增真实 scheduled-task-v2 浏览器域：真实构建 preview、Chrome、Console/Gateway/Runtime/Worker/PG/Redis/Artifact Store，以及可控本地模型/MCP/ASYNC 探针。
- [ ] 核心旅程：自然语言 Draft→用户确认→Fire→Agentic MCP+ASYNC Skill→记忆只读→Human→同 Execution resume→结果/指定版本产物可见；刷新后仍可读。
- [ ] 成功旅程不 route.fulfill/mock 业务 API；失败旅程只制造真实错误边界；明确 WebChat 零推送/通知、WeCom 路由和 Human、Fake MSSWWEB 契约不落路由。
- [ ] 补跨租户/用户/Agent、确认重放、两 Scheduler/Worker、早到/丢 hint/崩溃、多 operation、终态不复活等跨层检查；纯状态与高频竞态继续留各任务 unit/integration。
- [ ] 新增测试沿用既有目录/隔离库与 Redis 机制；新域接 Makefile/CI/changed journey 注册，移除旧列/旧路由 fixtures 与主动 WebChat delivery 断言，不另建 acceptance manifest。
- [ ] 声明边界验证通过后 finish；另行执行 make acceptance-e2e DOMAIN=scheduled-task-v2 与对应 CI 分片/全量，记录真实结果，未运行必须写待验证。
- [ ] 所有 TASK done 后停下等待用户完整功能验收和代码审查；明确确认前不 archive、不把测试失败写完成。

### Spec Delta

```json
[
  {
    "id": "DELTA-001",
    "op": "add",
    "target": "e2e/harness-e2e",
    "rule": "定时任务关键旅程必须由真实构建、浏览器及跨服务持久边界验证，覆盖用户确认、调度、工具挂起恢复、人工交互、结果与指定版本产物；定时任务的 pull surface 必须验证没有聊天投递或通知副作用。参数化 Kernel、竞态和授权逻辑在各自 unit/integration 层覆盖；组件或静态接线通过不能替代真实旅程结果，未执行的浏览器与人工场景必须保持待验证。",
    "evidence": "scheduled-task-v2.backend.design.md §3.5.14；scheduled-task-v2.scenarios.md，TASK-033 的真实域与 CI 接线。"
  }
]
```

### Log

- [2026-11-15] created (draft)

### Verification

- 尚未执行；此 TASK 的声明测试、业务场景均未宣称通过。