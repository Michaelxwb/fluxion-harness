---
{"design_template": "full"}
---

# 运行审计列表操作粒度 后端设计

> **文档编号**: MOD-AUDIT-OPVIEW-1.0
> **文档版本**: v1.0
> **创建日期**: 2026-11-17
> **文档状态**: 设计评审中
> **设计输入**: `.code-flow/tasks/2026-11-17/audit-operation-view/audit-operation-view.prd.md`

**评审边界说明**:
- **需求评审**: 第 2 章 → 通过后锁定需求基线。
- **设计评审**: 第 3-4 章 → 通过后锁定设计基线。
- **交接契约**: 2.5 验收条件；场景唯一源为 `audit-operation-view.scenarios.md`。

**ID 体系**: US、FEAT、API、RULE、NFR、RISK；场景使用 S-/E-/B-。

**本版关键结论（先读）**:

1. **只加读侧，不碰写侧**：Runtime、`AuditedModelProvider`、三张审计表的写入粒度与表结构一律不动；本需求全部改动落在 Console 的只读查询与前端展示。
2. **操作视图 = 现有投影的新增分支 + 外层过滤**：在四表 `UNION ALL` 投影中新增 `RUN` 分支（来源 `runtime.run_record`），默认视图在外层只保留 `RUN / CONFIG / EGRESS`。
3. **视图是显式参数**：列表新增 `view=operation|detail`；导出固定 `detail`，因此「列表变粗」不会传染到审计留档。
4. **步骤计数两阶段**：先分页取出当页操作行，再对当页 `run_id` 集合做一次 `GROUP BY` 聚合 + 一次「最近失败」查询；禁止逐行相关子查询，禁止每页全量预聚合。
5. **失败摘要与 Run 结果绑定**：只有 Run 自身失败时才展示失败步骤摘要；成功但有重试的 Run 只展示重试次数，不把重试渲染成失败。
6. **新增索引**：`runtime.run_record (tenant_id, create_time DESC) WHERE is_deleted = false`，支撑默认视图的租户 + 时间倒序分页。
7. **`RUN` 不提供审计详情**：列表首列/对象对 Run 行直接打开既有运行详情；`GET /audits/{type}/{id}` 仍只接受四类来源表，避免造出第二套运行详情。

---

## 2. 需求分析

### 2.1 需求概述

| 项目 | 内容 |
|------|------|
| **模块名称** | Console 运行审计列表（`apps/console-platform`） |
| **模块ID** | MOD-AUDIT-OPVIEW |
| **所属系统/产品线** | Fluxion Console 控制台 |
| **需求类型** | 功能优化（列表粒度） |
| **业务背景** | 列表是四表平铺投影，一次对话出现 6~10 行，模型审计还按 attempt 逐条落库；导航全部指向同一个运行详情。 |
| **核心目标** | 默认按业务操作成行（Run / 配置变更 / 出网），步骤折叠但可计数、可检索、可导出。 |

### 2.2 痛点与价值

| 维度 | 内容 |
|------|------|
| **目标用户** | 平台管理员、审计/合规查看者、值班排障人员 |
| **当前问题** | 一次对话 6~10 行，随轮次线性增长；步骤与操作同级，审计结论被淹没 |
| **业务影响** | 审计浏览成本高，失败定位需要逐条点开 |
| **预期价值** | 一次对话 1 行；失败在默认视图可见；留档完整性由导出与明细视图保持 |

**用户故事**：见 PRD US-01 ~ US-06。

### 2.3 功能方案

| 功能ID | 功能名称 | 后端实现要点 | 优先级 | 来源 |
|--------|---------|-------------|--------|------|
| FEAT-01 | 操作视图默认粒度 | 新增 `RUN` 投影分支；默认视图外层只保留 `RUN/CONFIG/EGRESS` | P0 | US-01, US-06 |
| FEAT-02 | 内部步骤列 | 两阶段聚合：计数 + 最近失败步骤；随列表行返回 | P0 | US-02 |
| FEAT-03 | 步骤明细视图 | `view=detail` 复用现有四类分支，行为与现状一致 | P0 | US-03 |
| FEAT-04 | 运行详情入口 | 不新增接口；`RUN` 不支持审计详情 | P0 | US-04 |
| FEAT-05 | 导出保持全量明细 | `all_rows()` 固定 detail 投影，导出列不变 | P0 | US-05 |
| FEAT-06 | Run 级投影与聚合查询 | 索引 + 两阶段聚合；EXPLAIN 验收 | P0 | US-01, US-02 |

#### 2.3.2 字段约束

新增投影列（仅 RUN 分支产出，其余分支为 NULL）：

| 字段 | 类型 | 规则 |
|------|------|------|
| `summary` | text | `run_record.input_text` 截断到 120 字符；**不参与关键字筛选**，不进导出列 |
| `steps_model` / `steps_tool` / `steps_egress` | int | 当页聚合结果；不落库、不缓存 |
| `steps_retries` | int | 模型 attempt 中 status 为重试的条数 |
| `failure_kind` / `failure_name` / `failure_code` | text | 仅 Run 结果为 `FAILED` 时有值；取该 Run 时间上最后一条失败步骤 |

### 2.4 范围与边界

| 类别 | 内容 |
|------|------|
| **范围（In Scope）** | `api/audits.py` 列表参数与出参；`audit_query_service` 校验与整形；`audit_query_repository` 投影与两阶段聚合；`runtime.run_record` 新索引迁移；后端测试。 |
| **非范围（Out of Scope）** | Runtime 写入链路与模型调用链；审计表结构与清理策略；概览统计与执行导出；RUN 类型的审计详情；Task 级操作行（OQ-03）。 |
| **有意妥协 / 技术债** | 只有 `task_id` 的执行（定时任务/后台任务）本期不聚合为操作行，仅在明细视图可见；同口径聚合作为后续增量。 |

### 2.5 验收条件

| 场景ID | 后端影响 / 备注 |
|--------|----------------|
| S-01 | 一次对话仅一行；默认视图过滤生效 |
| S-02 | 失败 Run 的 `failure` 字段与步骤计数正确 |
| S-03 | `view=detail` 与现状字段、筛选、分页一致 |
| S-04 | Run 行不提供审计详情；运行详情由前端直连既有接口 |
| S-05 | CONFIG / EGRESS 行字段口径不变 |
| S-06 | 导出在操作视图下仍输出全量明细 |
| E-01 | 主查询失败返回统一错误，不返回半截数据 |
| E-02 | 步骤聚合失败可见降级：`steps=null` 且记录 warning，不伪造 0 |
| B-01 | 分页在两个视图下各自按行语义计数 |
| B-02 | 只有 `task_id` 的记录不被静默丢弃 |

#### 2.5.3 非功能指标

| 指标ID | 类型 | 要求 |
|--------|------|------|
| NFR-PERF-01 | 性能 | 默认视图 EXPLAIN 不出现逐行 SubPlan 扫描审计表；同等数据量下 `count` 与分页耗时不超过改造前基线 |
| NFR-PERF-02 | 性能 | 改动不增加任何写入路径 |
| NFR-COMPAT-01 | 兼容 | 未传 `view` 时：`audit_type∈{MODEL,TOOL}` 自动走 detail（旧链接行为），其余走 operation |
| NFR-SEC-01 | 安全 | 所有分支继续按 `tenant_id` 过滤；关键字不新增对话正文字段 |
| NFR-TEST-01 | 质量 | 更新现有 7 个审计后端测试；新增投影分支、聚合、视图切换、导出用例 |

#### 2.5.4 验证环境假设

| 维度 | 假设 | 验证方式 / 参数 | 不满足时 |
|------|------|----------------|---------|
| 容量 | 本地 dev 库量级；租户内 Run 与审计事件均可造数 | pytest fixture 造 Run + 三类审计行 | 记录缺失项与 owner |
| 硬件 | 本机 PostgreSQL（共用容器） | `make acceptance` / 定向 pytest | 记录缺失项 |
| 依赖 | Console 后端可起；运行详情既有接口可用 | 真实 Stack 冒烟 | 记录缺失项 |
| 测试预算 | 后端 unit/integration 由 Task 声明；浏览器场景人工执行 | 人工核对任务文档 | 记录未覆盖风险 |

---

## 3. 技术设计

### 3.1 方案选型

#### 备选方案对比

| 方案 | 做法 | 优点 | 缺点 | 结论 |
|------|------|------|------|------|
| A. 新建 Run 级审计宽表 | 写入时聚合，列表只读宽表 | 查询最快 | 双写、历史回填、与三表一致性风险；触碰写侧 | 否决 |
| B. 查询期投影 + 两阶段聚合 | 新增 RUN 分支，分页后聚合当页 | 无新表、无回填、Runtime 零改动、可回退 | 需一条新索引；实现要守住两阶段 | **采用** |
| C. 前端按 trace 分组折叠 | 不动后端，前端 group | 改动最小 | 分页/总数语义失真；「模型调用不是审计事件」的根因未解 | 否决 |

#### 关键决策记录

| 决策 | 理由 |
|------|------|
| 视图由显式 `view` 参数表达，而不是从 `audit_type` 反推 | 导出固定 detail；列表语义与导出语义解耦，避免「导出跟着变粗」 |
| 未传 `view` 时 `MODEL/TOOL` 自动走 detail | 兼容既有链接与旧客户端，行为与改动前一致 |
| `RUN` 不提供审计详情 | 运行详情已存在且是唯一真相；避免第二套运行视图与字段漂移 |
| `summary` 与 `action` 分离 | 列表要显示用户提问摘要，但关键字筛选不得扫描对话正文；导出列也不扩 |

#### 技术栈

Python 3.12、FastAPI、SQLAlchemy 2（`text()` 原生 SQL 投影）、PostgreSQL、Alembic。

### 3.2 架构设计

```
GET /api/v1/audits                      apps/console-platform/backend/.../api/audits.py
  └─ AuditQueryService.list_audits      application/audit_query_service.py（校验 view/类型、整形出参）
       └─ AuditQueryRepository          infrastructure/repositories/audit_query_repository.py
            ├─ count(tenant, filters, view)       → 外层 count
            ├─ page(tenant, filters, view)        → 外层分页
            └─ attach_steps(tenant, run_ids)      → 当页步骤聚合（仅 operation 视图）
                 ▼
        control.config_audit_log / runtime.{tool_call_audit,egress_audit,model_invocation_audit,run_record}
```

#### 技术分层

| 层 | 职责 | 不做什么 |
|----|------|---------|
| API | 参数声明与封套 | 不做视图语义判断 |
| Service | `view`/`audit_type` 组合校验、行整形（steps/failure 组装） | 不写 SQL |
| Repository | 投影 SQL、两阶段聚合、索引命中的查询形态 | 不做字段命名转换以外的业务判断 |

### 3.3 数据设计

**无新表、无列变更。** 新增一个索引迁移 `0051`：

```python
op.create_index(
    "ix_run_record_tenant_create_time",
    "run_record",
    ["tenant_id", sa.text("create_time DESC")],
    schema="runtime",
    postgresql_where=sa.text("is_deleted = false"),
)
```

**RUN 分支投影映射**

| 投影列 | RUN 来源 |
|--------|---------|
| `audit_id` / `resource_id` / `run_id` | `run_record.id` |
| `audit_type` / `resource_type` | 常量 `RUN` |
| `actor_user_id` / `actor_name` | `run_record.user_id` / `control.platform_user.display_name` |
| `agent_id` / `agent_name` | `run_record.agent_id` / `control.agent_definition.name` |
| `action` | `COALESCE(run_record.channel_json->>'type', 'WEBCHAT')`（入口渠道，可被关键字命中） |
| `result_status` | `COMPLETED→SUCCESS`、`FAILED→FAILED`、`CANCELLED→CANCELLED`、其余（CREATED/RUNNING/WAITING_*）→`RUNNING`；筛选域 `RESULT_STATUSES` 相应新增 `RUNNING` / `CANCELLED`（加法，不影响既有取值） |
| `trace_id` | `run_record.trace_id` |
| `target` | `'RUN/' || id` |
| `occurred_at` | `COALESCE(start_time, create_time)` |
| `started_at` / `finished_at` | `start_time` / `end_time` |
| `latency_ms` | `EXTRACT(EPOCH FROM (end_time - start_time)) * 1000`（任一为空则为 NULL） |
| `task_id` | `NULL` |
| `summary` | `left(input_text, 120)` |

**步骤聚合（两阶段第二段）**

```sql
WITH s AS (
  SELECT run_id, 'MODEL' kind, status, NULL name, NULL error_code, create_time
    FROM runtime.model_invocation_audit
   WHERE tenant_id = :tenant_id AND run_id = ANY(:run_ids)
  UNION ALL
  SELECT run_id, 'TOOL', status, tool_name, error_code, create_time
    FROM runtime.tool_call_audit
   WHERE tenant_id = :tenant_id AND run_id = ANY(:run_ids)
  UNION ALL
  SELECT run_id, 'EGRESS', result_status, target, error_code, create_time
    FROM runtime.egress_audit
   WHERE tenant_id = :tenant_id AND run_id = ANY(:run_ids)
)
SELECT run_id,
       count(*) FILTER (WHERE kind = 'MODEL')  AS model_calls,
       count(*) FILTER (WHERE kind = 'TOOL')   AS tool_calls,
       count(*) FILTER (WHERE kind = 'EGRESS') AS egress_calls,
       count(*) FILTER (WHERE kind = 'MODEL' AND status NOT IN ('OK','SUCCESS')) AS retries
  FROM s GROUP BY run_id;
```

失败摘要单独一条（同一 `run_ids` 集合）：

```sql
SELECT DISTINCT ON (run_id) run_id, kind, name, error_code
  FROM s
 WHERE (kind = 'MODEL' AND status NOT IN ('OK','SUCCESS'))
    OR (kind = 'TOOL'  AND status IN ('ERROR','FAILED'))
    OR (kind = 'EGRESS' AND status IN ('ERROR','FAILED','DENIED','BLOCKED'))
 ORDER BY run_id, create_time DESC;
```

约束：
- 两条查询都只接受当页 `run_ids`（≤ `page_size`），走 `run_id` 前缀索引；
- 除 `RUN` 行外不发聚合查询；当页没有 RUN 行时直接跳过；
- 聚合结果只用于出参整形，不缓存、不落库。

### 3.4 接口设计

#### API-01: 审计列表（`GET /api/v1/audits`）

新增参数：

| 参数 | 类型 | 默认 | 说明 |
|------|------|------|------|
| `view` | `operation` \| `detail` | 见下 | 视图；未传时按 `audit_type` 推断（NFR-COMPAT-01） |
| `audit_type` | 枚举 | — | 新增 `RUN`；`MODEL/TOOL` 仅与 `detail` 组合 |

`view` 解析规则：

| 输入 | 结果 |
|------|------|
| `view=operation` + `audit_type ∈ {RUN, CONFIG, EGRESS}` 或未传类型 | 操作视图：外层保留 `RUN/CONFIG/EGRESS`（有类型时再收窄） |
| `view=detail` + `audit_type ∈ {MODEL, TOOL}` | 明细视图：该类事件行 |
| `view=detail` + 未传类型 | 明细视图：四类全量（与改动前一致） |
| `view=operation` + `audit_type ∈ {MODEL, TOOL}` | `COMMON_VALIDATION_ERROR`（语义冲突，不静默改写） |
| 未传 `view` + `audit_type ∈ {MODEL, TOOL}` | 自动按 `detail`（兼容旧链接） |
| 未传 `view` + 其他 | 按 `operation`（新默认） |

出参（`paginate` 封套不变），每项在现有字段基础上新增：

```json
{
  "audit_type": "RUN",
  "summary": "帮我起一个每周一早上九点的周报定时任务",
  "steps": {"model": 4, "tool": 2, "egress": 0, "retries": 1},
  "failure": {"kind": "TOOL", "name": "wecom_send", "code": "TOOL_VALIDATION_ERROR"}
}
```

- `steps` / `failure` 仅 `RUN` 行出现；其他类型为 `null`；
- `steps` 聚合失败时整项为 `null`（E-02），并写 warning 日志，**不返回 0**；
- `failure` 仅当 `result_status = FAILED` 时有值；
- `total` / `page` 按当前视图的行语义计算。

#### API-02: 审计详情（不变）

`GET /api/v1/audits/{audit_type}/{audit_id}` 只接受 `CONFIG/TOOL/EGRESS/MODEL`；`RUN` 返回 `COMMON_VALIDATION_ERROR`，由前端改为打开运行详情。

#### API-05/06: 审计导出（不变）

- 导出固定 `view=detail`，忽略任何视图参数；
- `audit_type` 仍只接受四类来源表；`RUN` 返回校验错误；
- 导出列不新增 `summary`。

### 3.5 质量实现方案

#### 性能设计

- 主查询：外层 `ORDER BY occurred_at DESC, audit_id DESC` + `OFFSET/LIMIT`；RUN 分支按 `(tenant_id, create_time DESC)` 索引取数；
- 计数：对投影外层 `count(*)`，操作视图候选集从事件行降到 Run 行；
- 聚合：当页 `run_id` 集合 + 三表 `run_id` 前缀索引（已有 `ix_*_audit_run`）；
- 验收：对默认视图与明细视图各跑一次 `EXPLAIN (ANALYZE, BUFFERS)`，确认无逐行 SubPlan、无全表预聚合。

#### 可靠性设计

- 主查询失败：按统一错误返回，不返回半截列表；
- 步骤聚合失败：该页 `steps` 置 `null`、记录 warning（含 tenant 与当页 run 数），列表其余字段照常返回；
- 导出失败保持现有任务态。

#### 安全性设计

- 所有分支按 `tenant_id` 过滤，聚合查询同样带租户谓词；
- 关键字筛选字段与现状一致（`action`/`resource_type`/`trace_id`/`actor`/`agent`/`resource_id`/`target`），**不含 `summary`**；
- 未新增明文敏感列；`summary` 只回列表，不进导出。

#### 可观测性设计

- 聚合降级 warning 日志（结构：`audit_steps_degraded`，含 `tenant_id`、`run_count`）；
- 不新增指标（审计页非高频路径）。

---

## 4. 部署与运维

### 4.1 部署架构

无新增服务；Console 后端与既有 PostgreSQL 即可。

### 4.4 数据迁移

- 迁移 `0051`：仅创建索引，不改表、不回填；
- 回滚：`op.drop_index("ix_run_record_tenant_create_time", table_name="run_record", schema="runtime")`；
- 大表上线时按既有迁移执行口径（`migrations/db_migrate.py`），索引创建耗时需在发布窗口内确认。

---

## 5. 风险与依赖

| 风险ID | 描述 | 影响 | 缓解 |
|--------|------|------|------|
| RISK-01 | 聚合写成逐行子查询 | 大租户审计页变慢 | 两阶段实现 + EXPLAIN 验收 + 代码评审检查 |
| RISK-02 | `view` 与 `audit_type` 组合语义被前端误用 | 翻页/总数错位 | 解析规则在 spec 与测试中固化 |
| RISK-03 | 新索引在热表上创建耗时 | 发布窗口风险 | 按现有迁移流程评估，必要时错峰 |
| RISK-04 | 步骤聚合失败被静默当成 0 | 审计展示失真 | `steps=null` + warning 日志 + 用例覆盖 |

---

## 6. 需求追溯矩阵

| US | FEAT | API | 场景 | 测试 |
|----|------|-----|------|------|
| US-01 | FEAT-01, FEAT-06 | API-01 | S-01, B-01 | `tests/console_platform/test_audit_query_api.py` |
| US-02 | FEAT-02 | API-01 | S-02, E-02 | 新增步骤聚合同步/集成用例 |
| US-03 | FEAT-03 | API-01 | S-03 | `test_audits_api.py`、`test_audit_query_api.py` |
| US-04 | FEAT-04 | API-02 | S-04 | `test_audit_detail_api.py` |
| US-05 | FEAT-05 | API-05/06 | S-06 | `test_audit_export_api.py`、`test_audit_export_download.py` |
| US-06 | FEAT-01 | API-01 | S-05 | `test_audit_config_write.py`、`test_audit_agent_filter.py` |

## Spec Compliance Matrix

| Spec/Rule | 落实点 |
|-----------|--------|
| `api/harness-api#RULE-api-001` | API-01 沿用 `{"items","page","page_size","total"}` 封套与分页边界 |
| `persistence/harness-data#RULE-data-001` | 迁移 0051 为分页排序键建索引 |
| `ui/harness-ui#RULE-ui-001` | 主展示字段仍是详情入口（Run 行开运行详情） |
| `time/harness-time#RULE-time-001` | 时间仍由 Console 共享格式化器输出 offset |
| `test/harness-test#RULE-test-001` | 集成测试走真实 PostgreSQL，不 mock 仓储 |

---

## 附录：术语表

| 术语 | 定义 |
|------|------|
| 操作视图 | 一行 = 一次业务操作（RUN / CONFIG / EGRESS） |
| 明细视图 | 一行 = 一次模型/工具/出网调用 |
| 步骤聚合 | 对当页 Run 的模型/工具/出网记录做计数与最近失败提取 |

---

*文档结束*
