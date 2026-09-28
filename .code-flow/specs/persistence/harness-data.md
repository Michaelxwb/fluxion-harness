---
id: harness-data
description: Agent Harness 通用平台规则：data
stages:
- design
- plan
- code
- review
enforcement: required
verifiers:
- rule: RULE-data-001
  type: command
  config:
    argv:
    - uv
    - run
    - pytest
    - -q
    - tests
    - -k
    - schema_parity
    cwd: .
    timeout: 600
---

# harness-data

## Rules

- [RULE-data-001] 产品表统一 `id/is_deleted/create_time/update_time`；软删除唯一约束用 partial unique `WHERE is_deleted=false`；时间统一 `timestamptz`；同 Owner Schema 用物理 FK，跨 Owner Schema 仅逻辑 UUID；JSON 配置用 `jsonb` 且关键查询字段不得只藏在 JSON。

## Conventions

- **跨 Owner Schema 的只读聚合**：允许直接读取其它 Owner 的表（如 `task.*`、`runtime.*`）做**只读聚合投影**，条件是：① 只读、不写任何表；② 每条查询都带 `tenant_id` 与 `is_deleted = false`（或该表的软删等价条件）；③ 名称类字段在同一 SQL 内 JOIN 批量补齐，不做逐行关联（N+1）。既有两例：`audit_query_repository`（11-audit-observability）与 `overview_query_repository`（12-overview-dashboard）。**不建宽表、不建物化视图**，跨 Schema 一律逻辑引用（UUID）、不建物理 FK。
- **四列口径的唯一载体是 `StandardColumnsMixin`**：`id`/`is_deleted`/`create_time`/`update_time` 四个声明集中在各 app 的 `infrastructure/models/base.py`，全仓 **38 个 ORM 类全部继承它，没有一个直接继承 `Base`**。三个 app 各持一份**同名同内容的副本**：`apps/console-platform/backend/src/muad_console_platform/infrastructure/models/base.py`、`apps/agent-worker/src/muad_agent_worker/infrastructure/models/base.py`、`apps/agent-runtime/src/muad_agent_runtime/infrastructure/models/base.py`（三份 `NAMING_CONVENTION` 也一致）。
  - ✅ `class X(StandardColumnsMixin, Base):` —— 四列自动齐全且口径一致。
  - ❌ 直接 `class X(Base):` 自己写四列 ⇒ 极易漏 `is_deleted`（软删条件失效）或写成裸 `datetime` 而非 `DateTime(timezone=True)`。
  - 注：三份副本无单一来源，改四列口径时必须同时改三处。
- **partial 索引不限于「软删唯一」**：状态谓词索引同样是 partial，用于把「活跃/运行中」这一小集合隔离出来。既有：`ix_task_execution_running_lease_until`（`WHERE status = 'RUNNING'`）与 `ix_task_execution_final_delivery`（`WHERE delivery_mode = 'FINAL_ONLY' AND is_deleted = false`，`apps/agent-worker/src/muad_agent_worker/infrastructure/models/task.py`）；`ix_run_record_running_lease_until` 与 `...（status IN ('CREATED','RUNNING','WAITING_INPUT') AND is_deleted = false）`（`apps/agent-runtime/src/muad_agent_runtime/infrastructure/models/runtime.py`）。
  - ✅ 按「查询只关心某个状态子集」建 partial 索引；❌ 给状态列建全量索引（写放大且选择性差）。
- **list 与 count 必须共用同一条件列表**：把过滤条件抽成一个私有 helper，`list()` 与 `count()` 同时引用，避免 `total` 与 `items` 漂移（既有范式：`ConsoleAccountRepository._active_conditions(tenant_id)` 被 `list()` 与 `count()` 复用，`apps/console-platform/backend/src/muad_console_platform/infrastructure/repositories/console_account_repository.py`）。
  - ✅ 两处 `where(*self._active_conditions(tenant_id))`。
  - ❌ 在 `count()` 里重写一遍过滤条件 ⇒ 后续只改一处时前端看到「总数 10 / 实际 8 条」。
- **`update_time` 没有 `onupdate`，由应用层显式赋值**：`StandardColumnsMixin` 只设 `server_default=now()`，`update_time` **不会**随 UPDATE 自动刷新（三份 `base.py` 同）。写入方须自己 `row.update_time = datetime.now(UTC)`（既有：`application/auth_service.py` 在失败计数递增与改密两处显式赋值）。
  - ✅ 任何 UPDATE 路径都显式赋 `update_time`。
  - ❌ 只改业务列 ⇒ `update_time` 停在上一次写库时刻，审计/增量同步据此判断会漏行。
- **Schema 名单固定 4 个**：`control`/`runtime`/`task`/`langgraph`（`migrations/versions/0001_create_schemas.py`）。前三个是本产品的 Owner Schema；`langgraph` 是 LangGraph 框架自有表（无 ORM 模型、不属产品表），**不得**在其中放业务表。
- **租户内存在性判断（规则正文在根 `CLAUDE.md` Core Principles；本 spec 已移除机检）**：任何「是否存在」判断（如启动自检）必须把 `count` 限定在目标租户内，不得用全库计数——全库判定会让任一租户有账号就掩盖「默认租户无账号 ⇒ 无法登录」的静默故障。**此处原先的 `no-global-count-in-tenant-check` 已删除**：它的 `pattern: 'count_all\('` 指向 13-console-auth 已移除的旧实现（全仓 0 命中），只防该法复活；而「无租户过滤的 count」这类通用形态正则在多行 SQL 上判不准、误报率高，留着是假防线。故本条**靠评审把关，不要以为有机检兜底**。
  - ✅ `await self._accounts.count(self._require_tenant()) > 0`；❌ `count_all() > 0`，或 `select(func.count())` 未带 `tenant_id` 条件

## Avoid

- 违反上述任一规则的实现必须修复；与此 Spec 冲突的文档以本 Spec 与 `docs/` V1.4 为准。
