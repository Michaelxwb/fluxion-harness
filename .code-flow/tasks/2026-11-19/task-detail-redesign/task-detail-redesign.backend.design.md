---
design_template: full
---

# 任务详情页改版 后端设计

> **文档编号**: MOD-TASKDETAIL-1.0
> **文档版本**: v1.0
> **创建日期**: 2026-11-19
> **文档状态**: 设计评审中

**评审边界说明**:
- **需求评审**: 第 2 章（需求分析）→ 通过后锁定需求基线
- **设计评审**: 第 3-4 章（技术设计 + 部署运维）→ 通过后锁定设计基线
- **交接契约**: 2.5 验收条件 — 需求定义 What，设计实现 How

**ID 体系**: FEAT、API、RULE、TC、RISK、NFR；场景引用 `task-detail-redesign.scenarios.md` 的 S-/E-/B-。

**需求来源**: 用户 2026-11-19 对话 brief + 本目录 `README.md`。

---

## 1. 文档控制

### 1.1 责任人

| 角色 | 姓名 | 职责范围 |
|------|------|---------|
| 产品经理 | fluxion-harness | 需求定义、业务验收 |
| 开发负责人 | fluxion-harness | 技术方案、代码实现 |
| 测试负责人 | fluxion-harness | 测试策略、质量保证 |

### 1.2 修订历史

| 版本 | 日期 | 作者 | 变更描述 |
|------|------|------|---------|
| v1.0 | 2026-11-19 | fluxion-harness | 初版：Fire/LastFire 增加端到端耗时与产物数；无数据库迁移 |

---

## 2. 需求分析

### 2.1 需求概述

| 项目 | 内容 |
|------|------|
| **模块名称** | Agent Worker 任务中心读投影 + 共享 contracts |
| **模块ID** | MOD-TASKDETAIL |
| **所属系统/产品线** | Agent Worker / Agent Runtime / Console WebChat |
| **需求类型** | 功能增强（读接口字段扩展） |
| **业务背景** | 前端改版后要在运行列表和首屏显示「用时 / 文件数」，现有 `FireItem` 与 `LastFireSummary` 没有这两个事实。 |
| **核心目标** | 在不改数据库 schema、不新增端点的前提下，为每个 Fire 返回端到端耗时与产物数。 |

### 2.2 痛点与价值

| 维度 | 内容 |
|------|------|
| **目标用户** | 任务中心用户；排查运行结果的支持人员 |
| **当前问题** | 运行列表只有 Fire/Execution 状态；用户要打开每次执行详情才知道耗时和文件数。 |
| **业务影响** | 多次运行对比成本高；「是否按计划完成、生成了什么」无法在列表层判断。 |
| **预期价值** | 一次列表请求返回用户可读的运行摘要，避免 N+1；首屏直接展示最近一次结果。 |

### 2.3 功能方案

| 功能ID | 功能名称 | 说明 | 优先级 | 来源 |
|--------|---------|------|--------|------|
| FEAT-B01 | `FireItem` 扩展 | 增加 `duration_ms` / `artifact_count`；运行列表一次返回 | P0 | 用户 brief |
| FEAT-B02 | `LastFireSummary` 扩展 | 增加 `execution_id` / `duration_ms` / `artifact_count`；首屏最近一次可直接展示 | P0 | 用户 brief |
| FEAT-B03 | 批量执行事实查询 | 一页 6 条 Fire 用批量查询取执行时间、最后投递、产物数，禁止 N+1 | P0 | 设计约束 |
| FEAT-B04 | 兼容与降级 | 新字段 additive；旧执行缺 `finished_at` 时返回 null；运行中返回 null | P0 | 设计约束 |
| FEAT-B05 | 测试与契约 | Worker 读 API、contracts、前端字段透传测试 | P0 | RULE-test-001 |

### 2.4 范围与边界

| 类别 | 内容 |
|------|------|
| **范围（In Scope）** | `packages/contracts/src/muad_contracts/scheduling.py` 的 `FireItem` / `LastFireSummary`；`apps/agent-worker/src/muad_agent_worker/application/schedule_read.py` 的 `fire_history` / `_last_fires`；相关 Worker/contracts 测试。 |
| **非范围（Out of Scope）** | 新数据库表/迁移；新 API 端点；Run-level 纯文本发送目标汇总；ZIP 打包下载；任务中心列表页字段；调度/执行/投递状态机。 |
| **前置假设** | `task.task_execution` 的 `started_at/finished_at` 已持久化；`runtime.artifact.task_id` 指向执行；`runtime.message_delivery` 的 `message_key` 为 `execution:{id}:final`。 |
| **有意妥协 / 技术债** | 端到端耗时依赖 `message_delivery` 最后送达时间；纯文本运行没有产物时 `artifact_count=0`，前端不显示发送目标数量。 |

### 2.5 验收条件

#### 2.5.1 业务规则与约束

| ID | 类型 | 描述 | 验证场景 |
|----|------|------|---------|
| RULE-RUN-01 | 业务规则 | 每次 Fire 的 `duration_ms` = `max(message_delivery.delivered_at)` 或 `task_execution.finished_at` 减去 `schedule_fire.scheduled_at`；无结束时间返回 null | S-01/S-02/E-03 |
| RULE-RUN-02 | 业务规则 | `artifact_count` 只统计 `artifact_type='OUTPUT'`，按 `logical_file_id` 去重，避免多版本重复计数 | S-01/S-04/B-05 |
| RULE-RUN-03 | 系统约束 | 一页 6 条 Fire 的执行事实必须批量查询，查询次数不随行数增长 | S-02/B-03 |
| RULE-RUN-04 | 系统约束 | 所有跨表读取带 `tenant_id` 与 `is_deleted=false`；跨 Owner 只读，不写 `runtime.*` | E-01/E-02 |
| RULE-RUN-05 | 系统约束 | 新字段 additive；旧前端忽略；旧执行缺字段时返回 null/0，不伪造时间 | E-03/E-04 |

#### 2.5.2 功能验收场景

> 场景唯一源：`task-detail-redesign.scenarios.md`。本表只保留设计影响。

| 场景ID | 设计影响 / 备注 |
|--------|----------------|
| S-01 | 首屏最近一次展示端到端用时与文件数 |
| S-02 | 运行列表每行展示用时/文件数；分页批量返回 |
| S-04/B-05 | 后台任务文件数沿用现有 `artifact_total`，不新增计算 |
| E-03 | 运行中/旧执行返回 `duration_ms=null`，前端显示 `—` |
| B-03 | 6/7 条运行的分页边界；批量查询稳定 |
| B-06 | 跳过/准入失败 Fire 无 `execution_id`，返回 null 与 0 文件 |

#### 2.5.3 非功能指标

| 指标ID | 指标名称 | 目标值 | 测量方法 |
|--------|---------|-------|---------|
| NFR-PERF-01 | 一页 6 条 Fire 的额外查询数 | ≤3 次，不随行数增长 | SQL 计数 / 测试断言 |
| NFR-PERF-02 | 一页 Fire 读取耗时 | 不高于现有列表读取基线 + 1 个批量查询量级（无硬编码阈值，按实测） | `pytest --durations` / 本地基准 |
| NFR-REL-01 | 字段读取失败 | 显式失败或返回 null，不伪造 0 秒/0 文件 | 单测注入 |

#### 2.5.4 验证环境假设

| 维度 | 假设 | 验证方式 / 参数 | 不满足时 |
|------|------|----------------|---------|
| 容量 | 单任务 24 次运行、每页 6 条；单个执行 0–8 个产物 | 真实 PG seed + Worker 测试 | 记录缺失项与 owner |
| 硬件 | CI PostgreSQL service；本地 PG 16 | CI matrix / 本地容器 | 记录缺失项与 owner |
| 依赖 | `task.task_execution`、`task.schedule_fire`、`runtime.artifact`、`runtime.message_delivery` 均有数据 | 真实数据库集成测试 | 记录缺失项与 owner |
| 测试预算 | Worker/contracts 集成测试；不新增浏览器 E2E；人工场景覆盖视觉与交互 | Task tests + 场景文件 | 记录未覆盖风险与 owner |

---

## 3. 技术设计

### 3.1 方案选型

采用**扩展现有 Worker 读投影**，不新增服务、不新增端点、不改数据库：

- `schedule_read.fire_history()` 已 outer join `TaskExecution.status`；在同一查询/同一请求内补充执行事实。
- `schedule_read._last_fires()` 已生成 `LastFireSummary`；复用同一批 helper。
- 现有 `_child_artifact_counts()` 与 `_artifact_deliveries()` 已证明跨 Schema 只读聚合可行；本次只新增按 execution 的批量事实查询。

备选方案对比：

| 方案 | 说明 | 结论 |
|------|------|------|
| A. 前端逐行调用 Execution 详情 | 无后端改动 | 拒绝：N+1，6 条列表最多 6 次额外请求 |
| B. 扩展 Fire 读投影 | 本方案 | 采用：一次列表请求返回 |
| C. 新建运行摘要表/物化视图 | 写放大、迁移成本 | 拒绝：当前数据量与读取频率不需要 |

### 3.2 架构设计

```text
WebChat TaskCenter
  GET /webchat/task-center/schedules/{id}/fires?page=N&page_size=6
        ↓
Agent Runtime webchat_history
        ↓ internal read
Agent Worker schedule_read.fire_history()
  ├─ task.schedule_fire  (Fire 台账)
  ├─ task.task_execution (started_at/finished_at/status)
  ├─ runtime.message_delivery (最后 delivered_at)
  └─ runtime.artifact (OUTPUT 产物数)
        ↓
FireItem { duration_ms, artifact_count, ... }
```

- 读取链路不变；只扩展返回 DTO。
- 不修改 Scheduler/Worker claim/lease/投递状态机。
- 不新增缓存；数据量按页 6 条、批量查询。

### 3.3 数据设计

**不新增表、不改列、不新增迁移。**

现有事实：

| 表 | 字段 | 用途 |
|----|------|------|
| `task.schedule_fire` | `scheduled_at`, `execution_id`, `status`, `skip_reason` | 计划时间与 Fire 状态 |
| `task.task_execution` | `started_at`, `finished_at`, `status` | 执行耗时回退 |
| `runtime.message_delivery` | `owner_type='TASK'`, `owner_id`, `message_key`, `delivered_at`, `status` | 端到端结束时间 |
| `runtime.artifact` | `task_id`, `logical_file_id`, `artifact_type`, `is_deleted` | 产物数 |

索引：

- `ix_artifact_task (task_id) WHERE task_id IS NOT NULL` 支撑按执行计数；
- `uq_message_delivery_tenant_key_target (tenant_id, message_key, target_key)` 支撑按执行键取最后送达；
- `ix_task_execution_schedule_create_time` 支撑最近一次 Fire/Execution 读取。

约束：

- 所有查询带 `tenant_id` 与 `is_deleted = false`（或对应软删等价条件）。
- 跨 Owner 只读聚合，不写 `runtime.*`；不建宽表/物化视图。
- `artifact_count` 使用 `count(DISTINCT logical_file_id)`；`version` 多版本不重复计数。

### 3.4 接口设计

不新增端点；扩展现有响应 DTO。

#### 3.4.1 `FireItem`

```python
class FireItem(ContractModel):
    fire_id: UUID
    scheduled_at: AwareDatetime
    status: FireStatus
    skip_reason: SkipReason | None = None
    missed_count: int = Field(default=0, ge=0)
    dst_resolution: DstResolution | None = None
    admission_error_code: str | None = None
    execution_id: UUID | None = None
    execution_status: TaskStatus | None = None
    duration_ms: int | None = None       # 新增：端到端耗时；未结束为空
    artifact_count: int = Field(default=0, ge=0)  # 新增：OUTPUT 产物数
```

响应示例：

```json
{
  "fire_id": "595a3eb2-6bd5-4e9b-97ee-8284746222c7",
  "scheduled_at": "2026-11-19T09:35:00Z",
  "status": "EXECUTION_CREATED",
  "execution_id": "b8c6d94b-3c2e-4b4a-88d1-6faf6a0eb905",
  "execution_status": "COMPLETED",
  "duration_ms": 16770,
  "artifact_count": 2
}
```

#### 3.4.2 `LastFireSummary`

```python
class LastFireSummary(ContractModel):
    scheduled_at: AwareDatetime
    status: FireStatus
    execution_status: TaskStatus | None = None
    missed_count: int = Field(default=0, ge=0)
    dst_resolution: DstResolution | None = None
    execution_id: UUID | None = None      # 新增：首屏拉取最近一次详情
    duration_ms: int | None = None        # 新增
    artifact_count: int = Field(default=0, ge=0)  # 新增
```

#### 3.4.3 兼容性

- 字段 additive；旧前端忽略，旧响应反序列化时新字段取默认值。
- `execution_id=null` 表示该 Fire 没有执行（跳过/准入失败），前端不请求 Execution 详情。
- `duration_ms=null` 表示尚未结束或旧执行缺结束时间；前端显示 `—`/进行中。
- `artifact_count=0` 表示没有 OUTPUT 产物；前端不显示「0 个文件」时可省略。

### 3.5 质量实现方案

#### 3.5.1 批量事实 helper

```python
@dataclass(frozen=True, slots=True)
class ExecutionRunFacts:
    duration_ms: int | None
    artifact_count: int

async def _execution_run_facts(
    self, *, tenant_id: str, execution_ids: Sequence[uuid.UUID]
) -> dict[uuid.UUID, ExecutionRunFacts]:
    # 1) task.task_execution: started_at / finished_at
    # 2) runtime.message_delivery:
    #    owner_type='TASK' AND owner_id IN ids AND is_deleted=false
    #    AND message_key = 'execution:' || owner_id::text || ':final'
    #    max(delivered_at) GROUP BY owner_id
    # 3) runtime.artifact:
    #    task_id IN ids AND artifact_type='OUTPUT' AND is_deleted=false
    #    count(DISTINCT logical_file_id) GROUP BY task_id
```

- 每页只调用一次；`fire_history` 与 `_last_fires` 复用。
- 三个查询都带 tenant/soft-delete；`IN` 参数使用 expanding bind。
- 查询失败按现有读错误处理；不返回伪造的 0 秒。`artifact_count` 无执行时为 0（事实：没有执行就没有产物）。

#### 3.5.2 耗时计算

```text
last_delivered = max(runtime.message_delivery.delivered_at)
finished_at    = task.task_execution.finished_at
end_time       = last_delivered ?? finished_at
duration_ms    = end_time ? max(0, round((end_time - fire.scheduled_at).total_seconds() * 1000)) : null
```

- 端到端口径：计划触发 → 最后一个投递目标送达。
- 无投递（`delivery_intent=NONE`）回退执行结束时间。
- 运行中/等待中/未结束返回 null。
- 该口径是当前默认；如果评审改为执行耗时，只把 `end_time` 改为 `finished_at`，DTO/前端不变。

#### 3.5.3 `_last_fires` 扩展

- 现有 `_last_fires` 取每个 Schedule 最近一条 Fire；扩展为：
  - 返回该 Fire 的 `execution_id`；
  - 用同一 helper 取 `duration_ms` / `artifact_count`；
  - 保持 `status/execution_status/missed_count/dst_resolution` 不变。
- 不为首屏额外增加查询端点；`GET /schedules/{id}` 的响应直接带新字段。

#### 3.5.4 错误与边界

| 边界 | 行为 |
|------|------|
| `execution_id=null` | `duration_ms=null`、`artifact_count=0`、`execution_status=null` |
| 运行中 | `duration_ms=null`；`artifact_count` 可统计已产生的 OUTPUT |
| `finished_at=null` 且无投递 | `duration_ms=null` |
| 旧执行无产物 | `artifact_count=0` |
| 多版本同一 `logical_file_id` | 只计 1 |
| 跨租户/软删 | 查询条件强制过滤；不读其他租户 |

#### 3.5.5 测试

- `tests/agent_worker/test_scheduled_v2_read_api.py`
  - Fire 列表字段与批量查询；
  - 端到端/回退/运行中三类耗时；
  - 产物去重；
  - 跳过/准入失败；
  - `last_fire` 与 Fire 列表同口径；
  - 查询次数不随 6 条行数增长。
- `tests/contracts/test_scheduled_v2_contracts.py`
  - 新字段 additive；旧 JSON 反序列化；
  - `artifact_count >= 0`；`duration_ms` 可空。
- `tests/gateway/test_scheduled_v2_task_center.py`
  - 封套字段透传。
- 前端组件/契约测试见前端设计。

---

## 4. 部署与运维

- 无数据库迁移、无新配置项、无新服务。
- 发布顺序：`packages/contracts` + Worker 先发布，WebChat 前端后发布；旧前端可忽略新字段。
- 监控：沿用现有任务中心读接口错误率/延迟；不新增指标。若批量事实查询出现慢查询，按 `pytest --durations` / PG `EXPLAIN` 定位，不先加缓存。
- 回滚：字段 additive；回滚前端即可恢复旧展示；Worker 查询可独立回滚，无数据修复。

---

## 5. 风险与依赖

| 风险ID | 描述 | 影响 | 应对 | 验证场景 |
|--------|------|------|------|---------|
| RISK-B01 | 端到端耗时口径与用户/产品预期不一致 | 展示值可能被理解为执行耗时 | 默认端到端并在文案/tooltip 说明；如改为执行耗时只换计算 | S-01/E-03 |
| RISK-B02 | 多机器人投递取最大 `delivered_at` | 最后一个目标慢会拉长耗时 | 这是端到端定义；不取平均/首个 | S-01 |
| RISK-B03 | `runtime.message_delivery` 查询跨 Owner | 读放大/边界风险 | 只读、带 tenant/soft-delete、批量 IN；不写 | E-01/E-02 |
| RISK-B04 | 多版本产物重复计数 | 文件数虚高 | `count(DISTINCT logical_file_id)` | B-05 |
| RISK-B05 | 旧执行缺 `finished_at` | 前端显示 `—` | 明确 null，不填 0 | E-03 |

**依赖**：前端设计 `task-detail-redesign.frontend.design.md`；场景文件 `task-detail-redesign.scenarios.md`。

---

## 6. 需求追溯矩阵

| 功能ID | 设计章节 | 场景 | 测试 |
|--------|---------|------|------|
| FEAT-B01 | §3.4.1 / §3.5.1 | S-02/B-03 | Worker read API |
| FEAT-B02 | §3.4.2 / §3.5.3 | S-01/E-03 | Worker read API |
| FEAT-B03 | §3.5.1 | S-02/B-03 | 查询计数/批量断言 |
| FEAT-B04 | §3.4.3 / §3.5.4 | E-03/E-04 | Contracts + Worker |
| FEAT-B05 | §3.5.5 | S-01~B-06 | Contracts/Worker/Gateway |

---

## Spec Compliance Matrix

| Spec/Rule | 设计影响 | 设计落点 | 验收场景 | 状态/N/A 理由 |
|-----------|---------|---------|---------|----------------|
| `api/harness-api#RULE-api-001` | 沿用统一封套与列表分页；新增字段不改变结构 | §3.4 | S-02 | applied |
| `worker/harness-worker#RULE-worker-001` | PostgreSQL 是 Fire/Execution 权威；本设计只读 | §3.2/§3.3 | S-01/S-02 | applied |
| `worker/harness-worker#RULE-003` | 投递事实只读聚合；不改投递状态/目标 | §3.5.1 | S-01 | applied（只读） |
| `persistence/harness-data#RULE-data-001` | 无新表/列/迁移；复用现有时间与索引 | §3.3 | — | confirmed N/A：无 schema 变更 |
| `persistence/harness-data` 跨 Owner 只读聚合约定 | 只读 `runtime.*`；带 tenant/soft-delete；批量 JOIN，不做 N+1 | §3.3/§3.5.1 | S-02/E-01 | applied（Convention） |
| `time/harness-time#RULE-time-001` | 内部 API 输出 ISO8601；耗时用 timestamptz 差值 | §3.5.2 | S-01 | applied |
| `test/harness-test#RULE-test-001` | Worker/contracts/Gateway 自动化；浏览器人工场景 | §3.5.5 | S-01~B-06 | applied |
| `e2e/harness-e2e#RULE-e2e-001` | 不新增 CI E2E；场景人工执行 | 场景文件 | S-01~B-06 | applied |
| `i18n/harness-i18n#RULE-i18n-001` | 本设计不新增后端错误文案 | — | — | confirmed N/A |

---

## 附录：术语表

| 术语 | 定义 |
|------|------|
| Fire | 定时任务的一次计划触发记录 |
| Execution | Fire 对应的真实执行 |
| 端到端耗时 | `schedule_fire.scheduled_at` 到最后一个 `message_delivery.delivered_at` |
| 产物数 | `runtime.artifact` 中 `artifact_type='OUTPUT'` 的不同 `logical_file_id` 数 |
| LastFire | Schedule 最近一次 Fire 的摘要事实 |
