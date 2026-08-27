# Tasks: Pinned Resource Retention（ADR-SNAPSHOT-001）

- **Source**: adr-snapshot-001-pinned-retention.design.md
- **Created**: 2026-08-27
- **Updated**: 2026-08-27

## Proposal

落地 ADR-SNAPSHOT-001：让 pinned 版本在 active 引用期间不可 hard-delete、deprecated 不影响在飞 Execution、resume 永不 resolve latest、hard-delete 走三重 guard。新增 `active_references` 表（追踪 execution/workflow/plugin_package 引用 owner）+ `ResourceStatus.TOMBSTONE` 软删状态 + `hard_delete` 三重 guard（active_ref → retention_period → GC safety）+ `recall_pinned` 拒绝 LATEST 回退，为 Workflow resume（Phase 3 ADR-WF-001）与 Plugin 卸载（ADR-EXT-001）提供 retention 契约与 active-ref 检查。

**范围裁剪声明**：RISK-01 的 TTL 兜底清理与 Execution lifespan 引用接线不在本切片（消费方 resume 是 Phase 3 ADR-WF；本切片只提供 add/release/check API 契约）；retention_period 具体值延后 Phase 6（RISK-02），guard 逻辑就位、值以参数注入（默认保守语义）；Artifact 大 payload 恢复随 Phase 5（§2.3 Out of Scope）。

---

## Acceptance Coverage

| 场景ID | 来源设计 | 测试层级 | 关键真实边界 | 负责任务 | 状态 |
|--------|---------|---------|-------------|---------|------|
| S-01 | adr-snapshot-001-pinned-retention.design.md#2.4.1 功能验收场景 | integration | resolver PUBLISHED-only check + snapshot pinned recall | TASK-004 | verified |
| S-02 | adr-snapshot-001-pinned-retention.design.md#2.4.1 功能验收场景 | integration | `active_references` 表 + delete guard | TASK-003 | verified |
| S-03 | adr-snapshot-001-pinned-retention.design.md#2.4.1 功能验收场景 | integration | delete 路径 + 三重 guard 顺序 | TASK-003 | verified |
| S-04 | adr-snapshot-001-pinned-retention.design.md#2.4.1 功能验收场景 | integration | status enum + resource_definitions 行保留 | TASK-003 | verified |
| E-01 | adr-snapshot-001-pinned-retention.design.md#2.4.1 功能验收场景 | integration | pinned-version recall API | TASK-002 | verified |
| E-02 | adr-snapshot-001-pinned-retention.design.md#2.4.1 功能验收场景 | integration | GC safety check（并发） | TASK-003 | verified |
| B-01 | adr-snapshot-001-pinned-retention.design.md#2.4.1 功能验收场景 | unit | active-ref check API（真实表查询） | TASK-001 | verified |
| B-02 | adr-snapshot-001-pinned-retention.design.md#2.4.1 功能验收场景 | unit | `ResourceStatus` 状态机 | TASK-002 | verified |

> 本表覆盖 design 全部 P0 场景（8/8）；RULE（4 required）→ 唯一 owner 映射见各任务 Spec-Refs；RISK-01→E-02（TTL 兜底标注 Phase 后续）、RISK-02→S-03（注入 retention_period）、RISK-03→E-02；NFR-ARCH-03（delete guard 0 违规）由 S-02/S-03/E-02 自动化承载，NFR-REL-03（hard-delete 幂等）由 E-02 承载。

---

## TASK-001: active_references 表 + 引用 add/release/check API

- **Status**: done
- **Priority**: P0
- **Depends**:
- **Source**: adr-snapshot-001-pinned-retention.design.md#2.2.2 字段约束, adr-snapshot-001-pinned-retention.design.md#3.3 接口设计, adr-snapshot-001-pinned-retention.design.md#3.4 性能与容量考量
- **Spec-Refs**: backend-database#RULE-backend-database-001
- **Acceptance-Refs**: B-01

### Description

新增 `active_references` 表（`schema.py`）：复合 PK（tenant_id/kind/resource_id/version/ref_id）+ ref_type 索引（`execution`/`workflow`/`plugin_package`）+ created_at 索引（retention period 判断）；选独立表而非计数字段是为追踪"谁引用"（rule 3/7 精确 owner 查询，§2.2.2 取舍）。实现三个 Registry 内部 API：`add_active_reference`（重复引用幂等）、`release_active_reference`（不存在 no-op）、`check_active_references`（PK 前缀 (tenant,kind,resource_id,version) 查询 + ref_type 过滤，目标 P95≤5ms）。

### Checklist
- [x] `schema.py` 新增 `active_references` 表：tenant_id/kind/resource_id/version/ref_type/ref_id/created_at；复合 PK (tenant_id,kind,resource_id,version,ref_id) 天然服务 check 的版本坐标前缀查询；Index (tenant_id,kind,resource_id,version,ref_type) 服务 ref_type 过滤；Index (tenant_id,created_at) 服务 retention 判断
- [x] `add_active_reference(tenant,kind,resource_id,version,ref_type,ref_id)`：重复引用幂等（同主键 no-op，不抛 IntegrityError）
- [x] `release_active_reference(...)`：不存在则 no-op
- [x] `check_active_references(tenant,kind,resource_id,version) -> list[ref]`：返回引用列表（含 ref_type/ref_id/created_at）
- [x] [B-01][unit] 真实 `active_references` 表（sqlite+aiosqlite + create_all，非 mock）→ 断言 add 后 check 返回该引用（ref_count>0 → 卸载语义上拒绝 `active_reference_blocked` 的数据基础）；release 后 check 为空（ref_count=0 放行）；重复 add 幂等（单行）；release 不存在 no-op。先写测试记录 RED（表未实现）
- [x] [backend-database#RULE-backend-database-001] verifier：active-references-schema——新表 + PK/索引设计（§3.4 check P95≤5ms 的索引路径）+ SQLite/PostgreSQL 共享 schema（S-02/E-02 引用本表）
- [x] 运行验收命令并填写 Acceptance Evidence

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|--------|---------|--------------------|---------|----------------|---------|------|
| B-01 | unit | `active_references` 真实表（sqlite+aiosqlite）add/release/check API | add 后 check 返回引用；release 后为空；重复 add 幂等单行；release 不存在 no-op | `backend/tests/unit/test_active_references.py::test_b01_*` | `uv run pytest backend/tests/unit/test_active_references.py -xvs` | verified |

### Acceptance Evidence

> `cf-task-start` 在编码期填写 RED/GREEN 结果、每个关键断言的位置和真实组件证据；全部状态 verified 后任务才可 done。

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|--------|-----|-------|---------|-------------|------|
| B-01 | FAIL: `ImportError: cannot import name 'add_active_reference' from 'fluxion.registry.resource_sqlalchemy'`（表与 API 未实现，4 用例 collection error） | `4 passed in 0.05s`（`uv run pytest backend/tests/unit/test_active_references.py -xvs`） | add→check 返回引用+ref_type 过滤+tenant scope：`backend/tests/unit/test_active_references.py:71-74`；release→check 空：`L103-118`（`test_b01_release_then_check_empty`）；重复 add 幂等单行：`L129-143`；release 不存在 no-op：`L146-162` | 真实表：fixture `sqlite+aiosqlite:///:memory:` + `metadata.create_all`（`active_references` 进 shared metadata，SQLite/PostgreSQL 同一 schema）；真实 SQL 路径 `resource_sqlalchemy.py::add/release/check_active_references`（方言 upsert ON CONFLICT DO NOTHING 幂等）；索引 `idx_active_reference_scope`/`idx_active_reference_tenant_created` + 复合 PK（`schema.py`）；回归 `contract+unit+integration 167 passed` | verified |

### Log
- [2026-08-27] created (draft)
- [2026-08-27] started (in-progress)
- [2026-08-27] completed (done)

---

## TASK-002: ResourceStatus TOMBSTONE 状态机 + tombstone 操作 + recall_pinned

- **Status**: done
- **Priority**: P0
- **Depends**:
- **Source**: adr-snapshot-001-pinned-retention.design.md#2.2.2 字段约束, adr-snapshot-001-pinned-retention.design.md#3.2 架构设计, adr-snapshot-001-pinned-retention.design.md#3.3 接口设计, adr-snapshot-001-pinned-retention.design.md#2.4.1 B-02/E-01
- **Acceptance-Refs**: B-02, E-01

### Description

`contracts.py` ResourceStatus 增 `TOMBSTONE`（soft-delete 标记：immutable payload 保留可恢复、不可解析）；`publish_sqlalchemy.py` `_next_status` 增分支（DEPRECATED→TOMBSTONE、PUBLISHED→TOMBSTONE，§3.2 状态机）。`tombstone(tenant,kind,resource_id,version,*,approval_id)` 走既有治理（audit_logs + publish_records + outbox，A8/A9/A20 模式），非法迁移抛 `VersionConflictError`，spec_json 原样保留。`recall_pinned(tenant,kind,resource_id,version)` 返回不可变 ResourceDefinition：版本不存在 `ResourceNotFound`；**拒绝 LATEST 选择器**（rule 6，resume 永不 resolve latest）；TOMBSTONE 版本仍可 recall（恢复语义）。

### Checklist
- [x] `contracts.py` ResourceStatus 增 `TOMBSTONE` 成员
- [x] `publish_sqlalchemy.py` `_next_status` 增 TOMBSTONE 分支：DEPRECATED→TOMBSTONE、PUBLISHED→TOMBSTONE；其余状态→TOMBSTONE 拒绝
- [x] `tombstone(tenant,kind,resource_id,version,*,approval_id)`：走既有治理（audit + publish_record + outbox）；非法迁移 `VersionConflictError`；不动 spec_json（immutable payload 保留）
- [x] `recall_pinned(tenant,kind,resource_id,version)`：不存在 `ResourceNotFound`；LATEST 选择器（`version="latest"` 等回退形态）拒绝；TOMBSTONE 仍可 recall
- [x] [B-02][unit] 状态机：DRAFT→PUBLISHED→DEPRECATED→TOMBSTONE 合法迁移通过；PUBLISHED→DRAFT 等非法迁移拒绝；published 后 immutable。先写测试记录 RED（TOMBSTONE 未实现）
- [x] [E-01][integration] 真实 store（sqlite+aiosqlite）recall_pinned → LATEST 选择器拒绝并强制返回 pinned version（rule 6）；pinned 版本 tombstone 后仍可 recall（恢复语义）；不存在版本 `ResourceNotFound`。先写测试记录 RED
- [x] 运行验收命令并填写 Acceptance Evidence

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|--------|---------|--------------------|---------|----------------|---------|------|
| B-02 | unit | `ResourceStatus` 状态机（`_next_status` 真实校验逻辑） | 合法迁移链通过；非法迁移拒绝；published 后 immutable | `backend/tests/unit/test_resource_status_tombstone.py::test_b02_*` | `uv run pytest backend/tests/unit/test_resource_status_tombstone.py -xvs` | verified |
| E-01 | integration | `recall_pinned` + 真实 store（sqlite+aiosqlite） | LATEST 选择器拒绝；tombstone 仍可 recall；不存在 `ResourceNotFound` | `backend/tests/integration/test_recall_pinned.py::test_e01_*` | `uv run pytest backend/tests/integration/test_recall_pinned.py -xvs` | verified |

### Acceptance Evidence

> `cf-task-start` 在编码期填写 RED/GREEN 结果、每个关键断言的位置和真实组件证据；全部状态 verified 后任务才可 done。

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|--------|-----|-------|---------|-------------|------|
| B-02 | FAIL: `PublicationOperation.TOMBSTONE` 未实现 → `test_b02_*` 4 failed（rollback 回归 1 pass 为既有行为） | `5 passed`（`uv run pytest backend/tests/unit/test_resource_status_tombstone.py -xvs`） | 合法链 DRAFT→PUBLISHED→DEPRECATED→TOMBSTONE：`backend/tests/unit/test_resource_status_tombstone.py:39,43,47`；PUBLISHED→TOMBSTONE 直达：`L55`；非法迁移拒绝（DRAFT→TOMBSTONE、TOMBSTONE 终态所有 op 拒绝）：`L72-76`；published 后 immutable（状态机无回 DRAFT 路径）：`L94` | 真实校验逻辑 `publish_sqlalchemy._next_status`（TOMBSTONE 分支 PUBLISHED/DEPRECATED→TOMBSTONE，其余 `VersionConflictError`）；`contracts.py` `ResourceStatus.TOMBSTONE` | verified |
| E-01 | FAIL: `recall_pinned`/`PublicationOperation.TOMBSTONE` 未实现 → ImportError（3 failed） | `3 passed`（`uv run pytest backend/tests/integration/test_recall_pinned.py -xvs`） | LATEST 选择器拒绝（latest/LATEST/latest-published）：`backend/tests/integration/test_recall_pinned.py:63`；tombstone 后仍可 recall + spec_json 保留：`L92-94`；治理落账 audit action=="tombstone"：`L98`；不存在 NotFound（含跨租户）：`L110,118` | 真实 store（`sqlite_store` fixture：sqlite+aiosqlite + `metadata.create_all`）；真实治理路径 `commit_publication(op=TOMBSTONE)`（audit + publish_record + outbox 同事务，A8/A9/A20 模式）；`recall_pinned` 经 `resource_sqlalchemy` 精确版本查询、DRAFT/不存在→`NotFoundError` | verified |

### Log
- [2026-08-27] created (draft)
- [2026-08-27] started (in-progress)；注：本任务无 owned required Rule（plan gate 唯一 owner 核验通过），`cf_spec_session` 因无 Spec-Refs 报 `spec_refs_missing` 属预期，无规则需要投影
- [2026-08-27] completed (done)

---

## TASK-003: hard_delete 三重 guard + GC safety check

- **Status**: done
- **Priority**: P0
- **Depends**: TASK-001, TASK-002
- **Source**: adr-snapshot-001-pinned-retention.design.md#2.2.2 字段约束, adr-snapshot-001-pinned-retention.design.md#3.2 架构设计, adr-snapshot-001-pinned-retention.design.md#3.3 接口设计, adr-snapshot-001-pinned-retention.design.md#2.4.1 S-02/S-03/S-04/E-02
- **Spec-Refs**: fluxion-resource-registry#RULE-fluxion-resource-001, fluxion-dfx#RULE-fluxion-dfx-001
- **Acceptance-Refs**: S-02, S-03, S-04, E-02

### Description

`hard_delete(tenant,kind,resource_id,version,*,approval_id,retention_period)`：三重 guard 固定顺序 active_ref → retention_period → GC safety check（§2.2.2 guard 表），任一失败返回对应错误码（`active_reference_blocked` / `retention_period_not_elapsed` / `gc_safety_check_failed`）且行保留；全过则物理删除 `resource_definitions` 行并走既有治理。retention_period 默认保守语义（RISK-02：Phase 6 前默认不放行，测试注入小周期验证通过路径）。GC safety check 在删除事务内二次确认无残留 active 引用（SELECT...FOR UPDATE 或等价行锁，E-02 并发安全）；hard-delete 幂等（NFR-REL-03：重复删除第二次为 no-op/NotFound）。

### Checklist
- [x] `hard_delete(...)` guard 顺序实现：先 active_ref（读 `active_references`）→ retention_period（tombstoned_at + period ≤ now）→ GC safety（删除事务内二次确认）；失败返回对应错误码不删行；成功物理删除 + 治理落账
- [x] retention_period 参数注入（timedelta），默认保守语义（Phase 6 前不因 retention 放行，RISK-02）
- [x] GC safety check 并发安全：删除事务内对 active_references 的二次确认（行锁/等价；SQLite 用事务内复查 + busy_timeout 基线 F5）
- [x] [S-02][integration] 真实 store：版本被 active workflow 引用（`active_references` 有行）→ hard-delete 拒绝 `active_reference_blocked`，`resource_definitions` 行保留。先写测试记录 RED
- [x] [S-03][integration] 真实 store：版本已 tombstone、active_ref=0、注入 retention_period 已过、GC check 通过 → 物理删除，`resource_definitions` 行不存在；guard 顺序断言（active_ref 优先于 retention）。先写测试记录 RED
- [x] [S-04][integration] 真实 store：v5 PUBLISHED→DEPRECATED→TOMBSTONE 后 spec_json 保留可恢复（recall_pinned 仍返回）；resolver 不解析 TOMBSTONE；active_ref>0 时不可 hard-delete。先写测试记录 RED
- [x] [E-02][integration] 并发 race：hard-delete 与引用建立并发 → 失败方 `gc_safety_check_failed`，不产生孤儿（引用在而定义被删）/重复删除；重复 hard-delete 第二次幂等（NFR-REL-03）。先写测试记录 RED
- [x] [fluxion-resource-registry#RULE-fluxion-resource-001] verifier：tombstone-active-ref-harddelete——Registry lifecycle 扩展（TOMBSTONE/active-ref/hard-delete）自动化证据（S-02+S-03 落地 + B-02 状态机落点在 TASK-002）
- [x] [fluxion-dfx#RULE-fluxion-dfx-001] verifier：gc-safety-automated-evidence——GC safety check + active-ref check 自动化证据（S-02+S-03+E-02 + B-01 在 TASK-001 落地）
- [x] 运行验收命令并填写 Acceptance Evidence

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|--------|---------|--------------------|---------|----------------|---------|------|
| S-02 | integration | `active_references` 表 + delete guard（真实 store） | active 引用时 hard-delete 拒绝 `active_reference_blocked`；行保留 | `backend/tests/integration/test_hard_delete_guards.py::test_s02_*` | `uv run pytest backend/tests/integration/test_hard_delete_guards.py -xvs` | verified |
| S-03 | integration | delete 路径 + 三重 guard 顺序（真实 store） | 全过物理删除；guard 顺序 active_ref→retention→GC | `backend/tests/integration/test_hard_delete_guards.py::test_s03_*` | `uv run pytest backend/tests/integration/test_hard_delete_guards.py -xvs` | verified |
| S-04 | integration | status enum + resource_definitions 行保留（真实 store + resolver） | TOMBSTONE 后 spec_json 保留、resolver 不解析、active_ref>0 不可 hard-delete | `backend/tests/integration/test_hard_delete_guards.py::test_s04_*` | `uv run pytest backend/tests/integration/test_hard_delete_guards.py -xvs` | verified |
| E-02 | integration | GC safety check（真实并发路径） | 并发失败方 `gc_safety_check_failed`；无孤儿/重复删除；重复删除幂等 | `backend/tests/integration/test_hard_delete_guards.py::test_e02_*` | `uv run pytest backend/tests/integration/test_hard_delete_guards.py -xvs` | verified |

### Acceptance Evidence

> `cf-task-start` 在编码期填写 RED/GREEN 结果、每个关键断言的位置和真实组件证据；全部状态 verified 后任务才可 done。

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|--------|-----|-------|---------|-------------|------|
| S-02 | FAIL: `DeleteResult`/`hard_delete` 未实现 → ImportError（collection error） | `7 passed`（`uv run pytest backend/tests/integration/test_hard_delete_guards.py -xvs`） | active_ref 拒绝 + 行保留：`backend/tests/integration/test_hard_delete_guards.py:109,113-116` | 真实 store（`sqlite_store` fixture）+ `active_references` 真实表 + `retention_sqlalchemy.hard_delete` guard#1 | verified |
| S-03 | 同上 RED | `7 passed` | guard 顺序 active_ref 优先 retention：`L130`；retention 未过拒绝：`L141`；全过物理删除（recall_pinned NotFound）：`L153-156` | `hard_delete` 固定 guard 顺序 + retention_period 注入（timedelta(0) 过 / days=1 不过）；tombstoned_at 取 publish_records tombstone 行 | verified |
| S-04 | 同上 RED | `7 passed` | TOMBSTONE spec_json 保留：`L174-175`；resolver 不解析 TOMBSTONE（ResourceVersionNotFoundError）：`L179`；active_ref>0 阻断：`L184` | 真实 resolver（PUBLISHED-only check，`resolver.py:144`）+ recall_pinned + 真实状态链 PUBLISHED→DEPRECATED→TOMBSTONE | verified |
| E-02 | 同上 RED | `7 passed`（并发用例 3/3 稳定） | 重复删除幂等（NotFound + 治理恰好 1 条）：`L197,205`；并发胜方 1/败方 1 + gc_safety_check_failed + 无孤儿：`L240-249` | 文件级 SQLite + WAL + busy_timeout（F5）双 store 真实写竞争（`file_store_pair` fixture）；GC safety = 删除事务内 `select_active_references` 二次确认 + CAS `status=TOMBSTONE` rowcount=0 → `gc_safety_check_failed` | verified |

### Log
- [2026-08-27] created (draft)
- [2026-08-27] started (in-progress)
- [2026-08-27] completed (done)

---

## TASK-004: deprecated 语义形式化 + pinned recall 不受 deprecated 影响

- **Status**: done
- **Priority**: P0
- **Depends**: TASK-002
- **Source**: adr-snapshot-001-pinned-retention.design.md#2.3 范围边界, adr-snapshot-001-pinned-retention.design.md#3.3 接口设计, adr-snapshot-001-pinned-retention.design.md#2.4.1 S-01
- **Spec-Refs**: fluxion-runtime-core#RULE-fluxion-runtime-001
- **Acceptance-Refs**: S-01

### Description

deprecated 语义形式化（design §2.3 In Scope(4)："已部分满足，补文档+测试"）：resolver 只 resolve PUBLISHED（`resolver.py:144` 既有）补 DEPRECATED/TOMBSTONE 不解析的显式测试；S-01 验证完整链路——v1 PUBLISHED→DEPRECATED、v2 PUBLISHED 后，新解析只返回 v2（rule 2）；在飞 Execution 按 snapshot pinned v1 经 `recall_pinned` 成功，不受 deprecated 影响。**RED 口径声明**：本任务为行为补测性质（resolver PUBLISHED-only 与 recall_pinned 分别已由既有实现与 TASK-002 满足），无法真实 RED 时按 green-before 记录原因，不得伪造失败。

### Checklist
- [x] [S-01][integration] 真实 resolver + store：v1 PUBLISHED→DEPRECATED、v2 PUBLISHED → 新解析只返回 v2；在飞 Execution 按 snapshot pinned v1 `recall_pinned` 成功返回 v1 定义（不受 deprecated 影响）。先写测试；无法 RED 时记录 green-before 原因（行为已由 resolver.py:144 + TASK-002 满足）
- [x] resolver 不解析 DEPRECATED/TOMBSTONE 的显式断言（补测，含 TOMBSTONE 新状态）
- [x] [fluxion-runtime-core#RULE-fluxion-runtime-001] verifier：snapshot-retention-recoverable——**Registry 层 applied**（TOMBSTONE 保留 spec_json + recall_pinned pinned 恢复；S-01+S-04 证据，S-04 落地在 TASK-003）；Artifact 层 + snapshot manifest 恢复随 Phase 5 TASK-I501..I504（对齐 design 矩阵 partial applied 口径）
- [x] 运行验收命令并填写 Acceptance Evidence

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|--------|---------|--------------------|---------|----------------|---------|------|
| S-01 | integration | resolver PUBLISHED-only check + snapshot pinned recall（真实 store + resolver） | deprecated 后新解析只返回 v2；pinned v1 recall 成功不受 deprecated 影响；DEPRECATED/TOMBSTONE 显式解析被拒 | `backend/tests/integration/test_deprecated_semantics.py::test_s01_*` | `uv run pytest backend/tests/integration/test_deprecated_semantics.py -xvs` | verified |

### Acceptance Evidence

> `cf-task-start` 在编码期填写 RED/GREEN 结果、每个关键断言的位置和真实组件证据；全部状态 verified 后任务才可 done。

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|--------|-----|-------|---------|-------------|------|
| S-01 | green-before（无真实 RED）：resolver 只 resolve PUBLISHED（`resolver.py:144` 既有）+ recall_pinned 不限状态仅拒 DRAFT/missing/LATEST（TASK-002 落地），行为补测首跑即 GREEN，不伪造失败 | `2 passed`（`uv run pytest backend/tests/integration/test_deprecated_semantics.py -xvs`） | 新解析只返回 v2（唯一 PUBLISHED）：`backend/tests/integration/test_deprecated_semantics.py:85-86`；pinned v1 recall 成功 + status DEPRECATED + spec_json 保留（不受 deprecated 影响）：`L92-94`；显式按 v1 解析被拒（DEPRECATED）：`L98`；DEPRECATED v3 显式被拒：`L123`；TOMBSTONE v4 显式被拒：`L127`；无 PUBLISHED 时 latest-published NotFound：`L131` | 真实 store（`sqlite_store` fixture：sqlite+aiosqlite）+ 真实 `ResourceResolver`（`_resolve_from_store` L144 `status != PUBLISHED → ResourceVersionNotFoundError`）+ 真实 `recall_pinned`（`resource_sqlalchemy` L398 仅拒 DRAFT/missing）+ 真实状态链 PUBLISHED→DEPRECATED→TOMBSTONE（`commit_publication` 治理） | verified |

### Log
- [2026-08-27] created (draft)
- [2026-08-27] started (in-progress)
- [2026-08-27] completed (done)
