# 第五轮 Review 裁决修复（D1~D16）

本轮把一次全量 Review 提出的 11 项契约缺陷、随之暴露的 7 个决策点（D1~D7）以及 31 张表的表级列漂移全部落到唯一事实源，并把"设计契约 ↔ 0001 迁移 ↔ ORM"的一致性变成可执行门禁（无豁免）。正确性契约不变，主要动作是**消除互相矛盾的第二解释**，不做兼容方案。

> 术语：`D1~D7` 是决策项编号（见下表），`D8~D16` 是文档内标注的修复点编号，`ADR-062..068` 是本轮新增的 ADR。

## 1. 决策与裁决（D1~D7）

| 编号 | 决策 | 裁决 | 理由（要点） | 落点 |
|---|---|---|---|---|
| D1 | 运行态取消的归属路径 | 取消**只有一个能力实现**，两个调用方适配：Console（Admin 会话）走 `EXE-API-03`；IM `/stop` 由 `CH-INT-02` → Runtime 调同一 Application（授权按可信 principal，只能是本人）。**不新增** `/internal/v1/executions/*/cancel` | 取消是幂等命令，`execution_command` 已是其唯一事实源；再加内部端点等于同一命令双入口、双份授权矩阵。授权应在 Application 层按可信身份判定，不按路由 | ADR-065、模块 05 `EXE-API-03`、模块 10 `CH-INT-02`、前端 `10`/`90` |
| D2 | 范围类型元数据的 Owner | Owner=**模块 12**（它装配 manifest 并持有 `ResourceScopeRegistry`），只读投影 `INT-API-01`；模块 05 与它共用同一注册表 Port，不复制第二份元数据 | 取值空间由 manifest 装配产生，Owner 只能是"把 manifest 变成契约"的模块；放 05 会形成"消费方持有生产方契约"的双份定义 | ADR-067、模块 12 `INT-API-01`、模块 05 SVC-API-11 段、前端 `02`/`90` |
| D3 | 能力凭据来源 `auth_mode` 的落点 | `implementation.auth_mode` 为**顶层必填**字段；判别式 Schema 四个分支都声明并列入 `required`；`config.auth_mode` 一律拒绝；列 `capability_implementation.auth_mode` 同步落库 | 原设计正文要求顶层字段、Schema 未声明且 `additionalProperties=false`、前端又放在 `config` 内——三处互斥导致两种提交都失败。凭据来源必须单点 | 模块 07 §3.3 合同与列、CAP-API-02/04 DTO、前端 `04`/`90` |
| D4 | ASYNC 的声明方式 | Step **显式声明** `execution_mode`（默认 SYNC，仅 CAPABILITY 可 ASYNC）+ 能力侧 `async_submittable` 标记，`SVC-API-05` **合取校验**；`reconcile_timeout_seconds`/`max_poll_attempts` 随 ASYNC 步骤进 Draft Schema | 执行模式若按能力语义隐式派生，同一 Draft 在不同时刻可编译出不同模式，破坏"执行固定 snapshot+release"；异步是调用契约而非能力属性 | ADR-062、模块 05 Draft Schema/编译段、模块 06 ASYNC 分支、模块 07 CAP-LIB-03、前端 `02`/`90` |
| D5 | 能力测试产物的归属与下载 | 能力测试产生**最小执行身份**（`execution_source=CAPABILITY_TEST`，`capability_id` 必填、`service_id`/`snapshot_id`/`release`/`draft_revision` 全空）；产物由该执行拥有，下载**只用 `EXE-API-06`** | 产物对外唯一身份是 `artifact_id`，其读取授权必须单点（以执行 FK 判定）。给测试另开下载契约或扩 `owner_type` 会让同一 artifact 有两套授权语义 | ADR-063、模块 05 执行源三态/`CAP-API-05`、模块 07、前端 `04`/`10` |
| D6 | 授权并发 | 授权改为**单条操作**（`POST .../grants`、`POST .../grants/{grant_id}/revoke`，`idempotency_key` 幂等），读侧返回逐条有效状态；**不引入集合版本/CAS** | 集合覆盖写在并发下结构性丢失更新；CAS 只是把丢失更新换成 409 重试，属于在错误模型上加锁。`agent_access_grant` 已有 `enabled/granted_by/granted_at/revoked_at` + pair 唯一约束，单条操作天然可寻址可审计 | ADR-064、模块 18 `USR-API-05/06/06R/07/08/08R`、前端 `03`/`09`/`90` |
| D7 | 数据库对齐的范围与方式 | 迁移与 ORM 由 ORM 元数据**全量重生成**并保持锁步；新增**设计↔迁移↔ORM 逐字段一致性门禁**；残余的表级列漂移进入显式 burn-down 清单（带 Owner） | 0001 基线原先由旧 ORM 快照机械生成，这正是"文档有列、物理无列"能通过评审的原因；只修个案不修生成路径，下一轮还会复发 | 本文件 §4、`tests/architecture/test_design_db_parity.py`、`migrations/versions/0001_initial_schema.py` |

## 2. 11 项 Review 发现的处置

| # | 级别 | 发现 | 处置 | 落点 |
|---|---|---|---|---|
| 1 | P1 | 同会话无法替换待确认提案：旧提案只置 `superseded_at`，仍满足 `status='PENDING' AND is_deleted=false` 的唯一索引条件 | 唯一谓词统一为 `status='PENDING' AND superseded_at IS NULL AND is_deleted=false`，且它就是"当前提案"的判定谓词；被取代行不再占槽位 | ADR-067（D8）、模块 05 §3.3 约束、`08` 索引行、ORM+迁移索引 |
| 2 | P1 | ASYNC 步骤有执行设计、无合法配置入口（Draft Schema 未声明 `execution_mode` 且禁止额外字段） | Draft Schema 增 `execution_mode`/`reconcile_timeout_seconds`/`max_poll_attempts` + ASYNC 仅限 CAPABILITY 的条件约束；补编译链与合取校验（Draft 阶段拦截 + Worker 运行期经 `CAP-LIB-03` 兜底 `async_submittable=false` 返回 422，不静默降级）；前端补控件 | ADR-062（D9）、模块 05、模块 06/07、前端 `02`/`90` |
| 3 | P1 | Capability 认证配置无法同时满足正文与 Schema（`implementation.auth_mode` 未在四个 Schema 分支声明，前端仍放 `config`） | 见 D3：顶层必填 + Schema 声明 + 列落库三处一致；`config.auth_mode` 明确拒绝 | 模块 07、前端 `04`/`90`、ORM+迁移 |
| 4 | P1 | 集群槽位对账会提前释放仍在运行的外部任务容量（按"租约有效的 RUNNING 步骤"统计，而 ASYNC 提交后已释放租约） | 占用生命周期与租约解耦：SYNC=终态释放；ASYNC=含对账收敛的终态才释放。新增 `execution_step.slot_resource_class` 持久占用标记，对账按它统计 | ADR-068（D16）、模块 06 §2.5/§3.3/S-WORK-13、模块 05 步骤表、ORM+迁移 |
| 5 | P1 | Console 无法终止普通运行中的执行（前端不调 `/cancel`，`/resume` 只接受 WAITING_HUMAN） | 见 D1：Console **必须**调 `EXE-API-03`；`EXE-API-05` 收窄为仅 WAITING_HUMAN 决策；补可取消状态集合与 409 语义 | ADR-065（D12）、模块 05 `EXE-API-03/05`、前端 `10`/`90` |
| 6 | P1 | 授权编辑仍会静默覆盖其他管理员的修改（前端承诺 409，后端明确不做乐观锁） | 见 D6：改为单条授权操作，丢失更新在结构上不可能 | ADR-064、模块 18、前端 `03`/`09`/`90` |
| 7 | P1 | Builder 执行可见范围存在相反定义（ADR/RULE 并集 vs 接口交集） | 统一为**并集**（ADR-052），两侧各自单独成立即可见，都不命中 404；补两侧独立验收 | ADR-067（D13）、模块 05 `EXE-API-01/02`、前端 `10` |
| 8 | P2 | 重新投递成功后汇总状态仍显示失败（聚合按 UNKNOWN/FAILED 优先且保留历史失败行） | `channel_delivery` 增 `message_key`（逻辑消息键），`delivery_status` 按逻辑消息取**有效尝试**聚合；同一 message_key 只允许一条非终态行（并发重投 409 `DELIVERY_IN_FLIGHT`） | ADR-066（D11）、模块 10 CH-LIB-02、模块 05 `EXE-API-07`、ORM+迁移、前端 `10` |
| 9 | P2 | 业务范围动态表单缺少元数据读取接口 | 新增 `INT-API-01 GET /api/v1/meta/resource-scope-types`（Owner=模块 12，内存投影、空集合 `items: []`、前端不硬编码） | ADR-067（D2）、模块 12、`09` 接口索引、前端 `02`/`90` |
| 10 | P2 | Builder "测试用户"选择链路未闭合（登录响应无 `user_id`、候选接口未定义、用户列表仅 Admin） | 新增 `AUTH-API-01 GET /api/v1/auth/me`（身份与角色的唯一读取入口）+ `SVC-API-11` 测试用户候选；登录契约保持冻结 | ADR-067（D14）、模块 09、模块 05、`06-接口设计基线`、前端 `02`/`90`/`00` |
| 11 | P2 | 能力测试产物的下载入口无法构造（只有 `artifact_id`，`EXE-API-06` 必须带 `execution_id`） | 见 D5：返回 `execution_id`，产物由 `CAPABILITY_TEST` 执行拥有，统一走 `EXE-API-06` | ADR-063（D10）、模块 05/07、前端 `04`/`90` |

## 3. 同步更新的验收场景

| 场景 | 断言 | 归属 |
|---|---|---|
| S-SVC-13 | 同会话连续签发提案：旧行置 `superseded_at`、**新行插入成功（无唯一冲突）**、当前提案查询只返回新的那条 | 模块 05 |
| S-SVC-14 | ASYNC 步骤配置链路：Draft 校验通过 → `execution_step.execution_mode=ASYNC` → `service_execution.execution_mode=ASYNC` → Worker 走 ASYNC 分支 | 模块 05 |
| S-SVC-15 | 能力测试产物归属与下载：产生 `CAPABILITY_TEST` 执行、`artifact.execution_id` 指向它、`EXE-API-06` 返回字节流、不进默认列表 | 模块 05 |
| S-SVC-16 | Builder 可见范围并集：仅满足"自建 Service"或"被授权 Agent"的执行分别可见；都不满足 404 | 模块 05 |
| S-SVC-17 | 重投后汇总收敛：新增 attempt 行且保留历史失败行，`delivery_status` 收敛 `DELIVERED`，再次重投 409 | 模块 05 |
| E-SVC-06 | 异步能力声明为同步（或反向）：`SVC-API-05` 阶段返回 `CAPABILITY_ASYNC_NOT_INVOKABLE`(422) 并定位到步骤 | 模块 05 |
| E-SVC-07 | 取消入口边界：RUNNING 走 `EXE-API-03` 接受；RUNNING 调 `EXE-API-05` 得 409；WAITING_HUMAN 两入口语义等价 | 模块 05 |
| E-SVC-08 | 并发重投：至多一条新建尝试，其余 409 `DELIVERY_IN_FLIGHT` | 模块 05 |
| E-USER-04 | 授权并发不丢失：两个 Admin 各加不同用户，**两条授权都生效** | 模块 18 |
| S-WORK-13（修订） | ASYNC 步骤提交后释放 lease 期间 `used` 不下降；对账后仍不超上限 | 模块 06 |
| S-10-06（修订） | Builder 可见范围为并集，与 ADR-052/`RULE-SVC-09`/`EXE-API-01` 完全一致 | 前端 10 |
| E-10-02（修订） | 重投成功后 `delivery_status` 收敛 `DELIVERED` | 前端 10 |

## 4. 数据库与门禁

### 4.1 迁移与 ORM 锁步重生成

- `migrations/versions/0001_initial_schema.py` 的 `CREATE TABLE`/`CREATE INDEX` 由 ORM 元数据**全量重生成**：44 表、114 条 DDL + 唯一计数器种子行，共 120 条语句。
- 语句按**依赖拓扑序**输出（被引用表在前），跨表环（`service_definition.current_release_id ↔ service_release.service_id`、`skill_artifact ↔ skill_definition`）以 `ALTER TABLE ... ADD CONSTRAINT` 延后到全部表建立之后，因此全新数据库可顺序执行建成。
- 本地空库（`isf_migration_check`，`DROP SCHEMA public CASCADE` 后重建）已逐条 `upgrade` 验证：**120/120 成功**。

### 4.2 本轮落库的列与约束

| 表 | 新增/变更 | 来源 |
|---|---|---|
| `execution_proposal` | 唯一索引谓词增 `AND superseded_at IS NULL` | D8 |
| `capability_implementation` | `auth_mode`（三值 CHECK，NOT NULL DEFAULT NONE）、`async_submittable`、`project_platform_id`（FK + PLATFORM_SERVICE 必填 CHECK）、`shared_secret_ref`、`ix_capability_impl_platform` | D3/D4 |
| `channel_delivery` | `message_key`（NOT NULL）+ `ix_channel_delivery_message(tenant_id,message_key,attempt)` | D11 |
| `execution_step` | `execution_mode`（SYNC/ASYNC CHECK）、`slot_resource_class`（三值 CHECK）+ `ix_execution_step_slot` | D4/D16 |
| `service_execution` | `capability_id`、`execution_source`（三值 CHECK）、`test_mode`、`draft_revision`、`execution_mode`、`cancel_requested_at`、`human_decision_at`、`context_summary`、`artifact_ids`、`root_execution_id`/`parent_execution_id`、`result_ref`、`error_code`、`error_message`、`started_at`/`finished_at`、`max_retries`、`lease_epoch`；**三分支执行源条件约束**（FORMAL/TEST/CAPABILITY_TEST 的字段形状）；`uq_service_execution_active_parent` 部分唯一索引 | D10/D12/D16 |
| `artifact` | `name`、`content_type`、`size_bytes`、`created_by`、`ix_artifact_execution_time` | D10 |

### 4.3 一致性门禁

新增两个架构门禁，共 23 条断言：

**`tests/architecture/test_design_db_parity.py`（9 条）**：

1. 迁移与 ORM 的表集合、列集合、索引清单必须完全一致（不一致即要求重生成基线）；
2. 模块设计中"有完整字段表"的表不得丢失文档列或新增未文档列，除非列在 `KNOWN_DESIGN_DRIFT`；
3. `KNOWN_DESIGN_DRIFT` 是**带 Owner 的 burn-down 清单**：已对齐的表必须移出，条目必须有归属模块与原因；
4. 本轮修复的列（`REVIEW_ROUND_COLUMNS`）单独设回归守卫，防止再次漂移。

**`tests/architecture/test_design_consistency.py`（14 条）**：把本轮"同一件事被两份文档写成相反口径"的根因钉住——可见范围不得再写交集、Console 必须走 `EXE-API-03`、授权不得再描述为全量集合覆盖、提案唯一谓词必须含 `superseded_at IS NULL`、`auth_mode` 不得回到 `config`、七个 ADR 必须在决策登记表、`CAPABILITY_TEST` 必须在四个 Owner 文档同步、被本轮触及的模块必须有变更行。历史记录目录（`05-变更记录/`、`04-追溯与验收/`）允许引用已废止措辞，规则文档只有显式标注为"已废止"时才可出现。

### 4.4 表级列对齐（P3，本轮闭合）

首次生成的迁移由**旧 ORM 快照**机械产出，导致 31 张表的列集合与设计文档不一致（命名对与缺列两类）。本轮按"设计文档为唯一事实源"逐表对齐，**burn-down 清单已清空**，门禁改为硬约束（空清单 + 新漂移即失败）。

**命名对齐**（设计名 ← 旧物理名）：

| 表 | 对齐 |
|---|---|
| `agent_definition` / `service_definition` / `capability_definition` / `skill_definition` / `model_config` | `key` ← `agent_key`/`service_key`/`capability_key`/`skill_key`；`description` ← `goal`；`model_config`：`protocol`/`base_url`/`model_name`/`default_parameters` ← `provider`/`model`/`config` |
| `agent_capability_binding` / `agent_skill_binding` / `agent_service_binding` / `agent_knowledge_binding` / `agent_access_grant` | `agent_definition_id` ← `agent_id`；`service_definition_id` ← `service_id`；`enabled` 落到三个 binding 表 |
| `execution_snapshot` | `snapshot_json`/`content_hash`/`service_id`/`source`/`draft_revision`/`test_mode`/`snapshot_ref` ← `snapshot_payload`/`service_content_hash`/`service_release_ref`/`agent_*`（删除非设计列），并补 FORMAL/TEST 条件约束 |
| `execution_step` | `output_json`/`error_code`/`error_detail_ref`/`input_json`/`result_ref`/`wait_until`/`artifact_ids`/`sequence_no`/`operation_id`/`source_step_id`/`checkpoint_ref` ← `result`/`error`（删除非设计列） |
| `async_task_run` | `provider_key` ← `provider`；新增 `operation_id`/`actor_user_id`/`capability_key`/`provider_type`/`provider_locator`/`project_platform_id`/`idempotency_key`/`input_hash`/`last_polled_at`/`max_poll_attempts`/`cancel_supported`/`result_ref`/`error_code`；唯一性改为 `operation_id`/`idempotency_key`（不再按 step 唯一，重试派生复用同一逻辑调用） |
| `execution_command` / `task_progress_event` | `payload_json` ← `payload`；`message` ← `payload`；补 `applied_at` |
| `artifact` | 删除非设计的 `metadata`；`name`/`checksum`/`content_type` 收紧为 NOT NULL |
| `capability_implementation` | `config` ← `config_ref`/`adapter` |
| `skill_artifact` / `skill_artifact_capability` / `skill_artifact_validation_event` | `manifest_json` ← `manifest`；`artifact_id` ← `skill_artifact_id`；`capability_id`/`capability_key_snapshot` ← `capability_key`；`status`/`report`/`actor` ← `result`/`report_ref`/`created_by` |
| `skill_import_preview` | `token_hash`/`staging_ref`/`actor_user_id`/`expected_*`/`status`/`committed_*` ← `preview_token`/`artifact_ref`；删除非设计列 |
| `channel_delivery` | `channel` ← `channel_type`；新增 `lease_owner`/`lease_expires_at`/`lease_epoch`/`provider_message_id`；`remote_idempotency` 改为 BOOLEAN |
| `channel_account` / `channel_identity` / `channel_delivery_route` | `agent_id` ← `default_agent_id`；`secret_ref`/`connection_metadata`/`last_connected_at` 补齐；`raw_identity_json` ← `raw_identity`；`channel_account_id` ← `account_id`；`last_active_at` 补齐 |
| `conversation` / `message` / `conversation_run` | 补 `origin_scope_key`/`next_message_sequence`/`last_message_at`、`message_type`/`trace_id`、`actor_user_id`/`cancel_requested_at`/`checkpoint_ref`，并补唯一键（`uq_conversation_active`、`uq_message_external`/`uq_message_sequence`、`uq_conversation_run_message`/`uq_conversation_run_active`） |
| `workspace` / `user_memory` / `platform_user` / `project_platform` / `user_project_credential` | `backend_type`/`workspace_ref` ← `storage_backend`/`root_ref`；`quota_bytes`/`revision`/`description`/`auth_type` 补齐；凭据表补 session generation 与 refresh 租约组 |
| `audit_log` | `before_digest`/`after_digest` ← `before_ref`/`after_ref`；补 `execution_id`/`result`/`occurred_at` |
| `bind_code` / `channel_binding` | 补 `created_by`；补 `binding_method` |

**协调表形态修复**（收尾自检发现，非原 11 项之一）：`worker_slot_counter` 此前只有"列名"对齐，形态仍与设计/ADR-056 相反——设计（模块 06 §3.3 与 ADR-056）要求 `resource_class` 为主键、恒 3 行、**无 `is_deleted`、无 `tenant_id`**，物理表却是代理 `id` 主键 + `tenant_id` + `is_deleted` + `resource_class` 唯一索引。本轮改为：PK = `resource_class`，删除 `id`/`is_deleted`/`tenant_id`，保留 `used`/`slot_limit`/`create_time`/`update_time`；迁移种子行同步去掉这三列。理由：加上 `tenant_id`/`is_deleted` 会让原子抢占 `UPDATE ... WHERE resource_class=:rc AND used<slot_limit` 可能命中错误行或留下孤儿计数，集群并发上限随之失效。该表是全库**唯一**的公共字段豁免，由 `tests/architecture/test_database_common_fields.py` 的 `COORDINATION_TABLES` 正反两向守卫（豁免公共字段检查 + 断言不得新增业务列且 PK 必须是 `resource_class`），`test_design_db_parity.py` 的 `NO_COMMON_COLUMNS` 同步该豁免。

**设计侧补齐**（物理列先于文档存在，属文档遗漏，已回写）：`service_execution.current_step_name`（ADR-051）、`skill_artifact.name`；并新增两张此前无字段表的表：`auth_account`/`auth_session`（模块 09 §3.3）。`knowledge_source`/`agent_knowledge_binding` 属 V1 显式后置（总设：Knowledge 不在 Phase 1 固定表），登记在门禁的 `UNDOCUMENTED_BY_DESIGN`，一旦模块设计补字段表即自动失败提醒移出。

**代码侧同步**：仓储/适配器/测试的列名与构造参数全部对齐（`service_key`→`key`、`agent_id`→`agent_definition_id`、`snapshot_payload`→`snapshot_json`、`after_ref`→`after_digest`、`default_agent_id`→`agent_id` 等）；`framework/domain/execution.py` 的 `ExecutionSnapshot`/`ServiceExecution` 收敛为设计的身份字段 + `snapshot_json` 投影；`ServiceDraft.goal`（草稿载荷字段）与能力契约参数 `service_key`/`capability_key`（非列名）保持不变。路由门禁改为断言 `channel_account.agent_id` 存在、`default_agent_id` 不得回归。

## 5. 验证结果

| 验证 | 结果 |
|---|---|
| `uv run pytest -q` | **106 passed**（本轮开始前为 69 passed / 11 failed） |
| `tests/architecture` | 54 passed（原 28 + 12 条 DB 一致性门禁 + 14 条文档一致性门禁） |
| `tests/integration`（真实 PG） | 15 passed（本轮开始时 11 项失败：`service_definition.created_by` NOT NULL 与旧测试夹具不一致；已补共享 `test_actor_id` 夹具与用例签名，未放宽任何 NOT NULL 约束） |
| 迁移可用性 | 空库顺序执行 **129/129** 语句成功（44 表），`downgrade` 44/44 成功；计数器预置 3 行（browser 2 / external-scan 2 / large-report 1）已复核；本地 `isf` 已按单基线重建 |
| 文档一致性 | 模块 02-README/05/06/07/09/10/12/18、`00-总体设计/03-核心设计决策`（ADR-062..068）、`01-架构与规范/06/08/09/10/11`、前端 `00/02/03/04/09/10/90/README` 同步；由 `test_design_consistency.py` 常态校验 |

## 6. 结论

11 项发现全部成立并已闭合；7 个决策点按"架构合理优先、不做兼容方案"的裁决落地（ADR-062..068）；**31 张表的表级列漂移已全部对齐，burn-down 清单清空**（§4.4），门禁从"允许带 Owner 的豁免"收紧为"有漂移即失败"。本轮新增的两道架构门禁把"设计契约 ↔ 0001 迁移 ↔ ORM"与"同一规则不得被两份文档写成相反口径"从人工抽查变为可执行检查，因此**下一轮 review 不会再以同样方式发现同类缺陷**。
