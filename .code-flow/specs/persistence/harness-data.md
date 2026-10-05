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
- **`migrations/` 下的脚本不得读取环境变量**：连接串与配置一律来自 `migrations/alembic.ini`（`migrations/_config.py` 的 `load_dsn` 明确**不做 env 回落**，缺失或空值直接 `SystemExit`）。理由不是洁癖：env 回落会造成「以为在升 A 库、实际升了 B 库」——CI、验收建库与本地 dev 各有各的 `DATABASE_URL`，一旦脚本自己去读，升级目标就取决于调用者的环境而不是显式传入的 ini。
  - ✅ `load_dsn(resolve_ini(given, root), "sqlalchemy.url", purpose=...)`；`migrations/db_migrate.py` / `env.py` / `bootstrap_db.py` 都走它
  - ❌ `os.environ["DATABASE_URL"]` / `os.getenv(...)` 出现在 `migrations/**`（全仓现为 **0 命中**，改动后必须仍是 0）
  - 来源：用户 2026-10-01 明确要求（「migrations/中的脚本不要读取环境变量，直接改成读取 `migrations/alembic.ini` 中的配置」）；本条**无机检**，靠评审把关
- **租户内存在性判断（规则正文在根 `CLAUDE.md` Core Principles；本 spec 已移除机检）**：任何「是否存在」判断（如启动自检）必须把 `count` 限定在目标租户内，不得用全库计数——全库判定会让任一租户有账号就掩盖「默认租户无账号 ⇒ 无法登录」的静默故障。**此处原先的 `no-global-count-in-tenant-check` 已删除**：它的 `pattern: 'count_all\('` 指向 13-console-auth 已移除的旧实现（全仓 0 命中），只防该法复活；而「无租户过滤的 count」这类通用形态正则在多行 SQL 上判不准、误报率高，留着是假防线。故本条**靠评审把关，不要以为有机检兜底**。
  - ✅ `await self._accounts.count(self._require_tenant()) > 0`；❌ `count_all() > 0`，或 `select(func.count())` 未带 `tenant_id` 条件
- **摘要文本是权威历史（落库），transcript 是逐字存档（落共享产物）——二者不得互换**：`CONTEXT_SUMMARY` 事件把五字段摘要落 `runtime.canonical_event`，重建时取 `seq` 最大的一份作前缀、其后再按 seq 重放后续事件，因此换 Pod 得到的历史逐字节相同（`packages/agent-core/src/muad_agent_core/context/summary.py:1-6,147-152`；`apps/agent-runtime/src/muad_agent_runtime/application/context_events.py:6,29-56,85-100`）。被摘要覆盖掉的**逐字原文**另落共享产物（类型 `TRANSCRIPT`，DB 只留相对 `storage_key`），且**只写不读**——本需求不新增任何对外读取/下载/明文导出端点（`apps/agent-runtime/src/muad_agent_runtime/application/attachments/transcripts.py:1-6,126-132`）。**互换的后果**：产物过了保留期会被清理（文件与行一起删，`apps/console-platform/backend/src/muad_console_platform/application/artifact_cleanup_service.py:136-145`），若把摘要只存成文件，被压缩掉的那段历史就净丢了；若把 transcript 存进库，等于把逐字原文塞进事件表。
  - ✅ 摘要在 `canonical_event`、逐字原文在产物存储（`CONTEXT_SUMMARY.payload.transcript_artifact_id` 只作引用字段）
  - ❌ 摘要只写文件（保留期一过即失）／把 transcript 塞进 `canonical_event` 的 payload
- **操作者/审计引用即使同 Owner Schema 也用逻辑引用，不建物理 FK**：`actor_user_id` 一类字段记录「谁做的」，指向 `console_account.id`，但**不建物理 FK**——版本行/审计行不可变且永不清理，物理 FK 会把操作过的账号永久钉住（账号删不掉），而记录本身只需保留 UUID 形状。先例两处：`ConfigAuditLog.actor_user_id`、`PlatformSetting.actor_user_id`（`apps/console-platform/backend/src/muad_console_platform/infrastructure/models/control.py:295,302,622-624,646`）。
  - 与 `RULE-data-001`「同 Owner Schema 用物理 FK」的字面口径有张力：那条规则管的是**业务实体间**的归属关系（可级联、需一致性）；**操作者/审计引用**是显式例外，按「逻辑 UUID + 不建 FK」处理。
  - ✅ `actor_user_id: Mapped[uuid.UUID | None] = mapped_column(sa.Uuid())`（无 `ForeignKey`）
  - ❌ 给审计/版本表的 `actor_user_id` 加 `ForeignKey("control.console_account.id")` ⇒ 账号一旦被清理，历史行要么被级联删、要么外键报错，审计链断裂。
- **平台设置版本表是 append-only**：`control.platform_setting` 每次保存**插入**一行新 `revision`（从 1 起），当前版本 = 该租户 `max(revision)`；租户无任何版本行时按 `revision=0` + schema 默认语义处理。`is_deleted` **恒为 `false`**（版本行不软删、不物理删）；**乐观并发不用行锁**——落败的 `INSERT (tenant_id, revision=expected+1)` 撞 partial unique `uq_platform_setting_tenant_revision`（`WHERE is_deleted = false`）即归一为版本冲突；**回滚也是插入新版本**（内容等于目标版本、写 RESTORE 审计），历史行全部保留（`apps/console-platform/backend/src/muad_console_platform/infrastructure/models/control.py` 的 `PlatformSetting`；`application/platform_settings_service.py`）。
  - ✅ 保存/回滚都 `INSERT` 新 `revision`；读当前取 `max(revision)`；并发落败者靠 partial unique 报 `PLATFORM_SETTINGS_VERSION_CONFLICT`
  - ❌ `UPDATE ... SET revision = revision + 1` 或 `UPDATE settings_json`（改写历史、丢 before/after）；把 `is_deleted` 置 `true`（使「当前 = `max(revision) WHERE is_deleted = false`」的谓词失去意义）

## Avoid

- 违反上述任一规则的实现必须修复；与此 Spec 冲突的文档以本 Spec 与 `docs/` V1.4 为准。
