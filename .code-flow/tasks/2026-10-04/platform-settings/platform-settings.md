# Tasks: 平台系统设置（配置归属、生效边界与系统设置页）

- **Source**: .code-flow/tasks/2026-10-04/platform-settings/（`platform-settings.backend.design.md` + `platform-settings.frontend.design.md`）
- **Created**: 2026-10-05
- **Updated**: 2026-10-05

## Proposal

平台业务默认值今天散落在四个服务的源码常量与 `.env` 里：改一个默认要发版或重启，上下文压缩的平台设置源恒返回空字典、且被 10 秒 TTL 缓存挡住了「保存即生效」，盘点还照出 10 组重复/冲突声明。本需求建立一份**按租户持久化、带版本、可审计**的平台业务设置作为唯一权威源，Console 提供系统设置页，四个服务在**业务操作边界**取一次快照（新 Run/Task 冻结、执行中对象不变），并收敛重复默认源、把环境项与代码项归属写死。设计要点见后端 design 的 ADR-01..ADR-10。

---

## Acceptance Coverage

| 场景ID | 来源设计 | 测试层级 | 关键真实边界 | 负责任务 | 状态 | 执行命令 | cwd | timeout | depends_on |
|--------|---------|---------|-------------|---------|------|---------|-----|---------|-----------|
| S-01 | platform-settings.frontend.design.md#2.4 验收条件 | E2E | 真实浏览器 → 真实 Console API → 真实 PostgreSQL → 真实 Runtime → 真实模型 HTTP 探针 | TASK-010 | e2e_deferred | - | . | 600 | |
| S-02 | platform-settings.backend.design.md#2.5.2 验收场景 | E2E | 真实 Console API → 真实 PostgreSQL → 真实 Worker 进程（真实 lease/claim） | TASK-006 | e2e_deferred | - | . | 600 | |
| S-03 | platform-settings.frontend.design.md#2.4 验收条件 | E2E | 真实浏览器（真实登录会话与角色）→ 真实 Console API → 真实 PostgreSQL | TASK-010 | e2e_deferred | - | . | 600 | |
| E-01 | platform-settings.backend.design.md#2.5.2 验收场景 | integration | 真实 PostgreSQL + 真实 settings service | TASK-004 | verified | ["uv","run","pytest","-q","tests/console_platform/test_platform_settings_api.py","-k","invalid_payload"] | . | 300 | |
| E-02 | platform-settings.backend.design.md#2.5.2 验收场景 | integration | 真实 PostgreSQL（真实唯一约束） | TASK-003 | verified | ["uv","run","pytest","-q","tests/console_platform/test_platform_settings_service.py","-k","version_conflict"] | . | 300 | |
| E-03 | platform-settings.backend.design.md#2.5.2 验收场景 | integration | 真实 Runtime 进程 + 被切断的 Console 内部端点 | TASK-005 | verified | ["uv","run","pytest","-q","tests/agent_runtime/test_platform_settings_source.py","-k","source_unavailable"] | . | 600 | |
| E-04 | platform-settings.backend.design.md#2.5.2 验收场景 | integration | 真实 Console API（真实服务身份校验） | TASK-004 | verified | ["uv","run","pytest","-q","tests/console_internal/test_platform_settings_internal.py","-k","service_identity"] | . | 300 | |
| E-05 | platform-settings.backend.design.md#2.5.2 验收场景 | integration | 真实 PostgreSQL（同一事务） | TASK-003 | verified | ["uv","run","pytest","-q","tests/console_platform/test_platform_settings_service.py","-k","audit_same_transaction"] | . | 300 | |
| E-06 | platform-settings.backend.design.md#2.5.2 验收场景 | integration | 真实 PostgreSQL | TASK-004 | verified | ["uv","run","pytest","-q","tests/console_platform/test_platform_settings_api.py","-k","restore"] | . | 300 | |
| E-07 | platform-settings.backend.design.md#2.5.2 验收场景 | integration | 真实 PostgreSQL + 真实 Agent 定义行 | TASK-005 | verified | ["uv","run","pytest","-q","tests/agent_runtime/test_platform_settings_source.py","-k","agent_override_precedence"] | . | 600 | |
| E-08 | platform-settings.backend.design.md#2.5.2 验收场景 | integration | 真实 PostgreSQL + 独立进程（无 TTL 缓存、无重启） | TASK-005 | verified | ["uv","run","pytest","-q","tests/agent_runtime/test_platform_settings_source.py","-k","new_revision_without_restart"] | . | 600 | |
| E-09 | platform-settings.backend.design.md#2.5.2 验收场景 | integration | 真实源码树 + 真实 `.env.example` + 真实 `SharedSettings` 字段集 | TASK-011 | verified | ["uv","run","pytest","-q","tests/test_configuration_convergence.py"] | . | 300 | |
| E-10 | platform-settings.backend.design.md#2.5.2 验收场景 | integration | 真实 PostgreSQL + 真实 HTTP 响应体 | TASK-004 | verified | ["uv","run","pytest","-q","tests/console_platform/test_platform_settings_api.py","-k","secret_rejected"] | . | 300 | |
| E-11 | platform-settings.frontend.design.md#2.4 验收条件 | E2E | 真实浏览器 + 真实 Console API（后端校验真实生效） | TASK-010 | e2e_deferred | - | . | 600 | |
| E-12 | platform-settings.frontend.design.md#2.4 验收条件 | E2E | 真实浏览器 + 真实 Console API（真实 409） | TASK-010 | e2e_deferred | - | . | 600 | |
| E-13 | platform-settings.frontend.design.md#2.4 验收条件 | E2E | 真实浏览器 + 真实路由；读取失败用真实网络失败注入（错误路径允许，成功路径禁止拦截） | TASK-010 | e2e_deferred | - | . | 600 | |
| E-14 | platform-settings.frontend.design.md#2.4 验收条件 | E2E | 真实浏览器 + 真实登录会话（真实 403，无路由拦截） | TASK-010 | e2e_deferred | - | . | 600 | |
| E-15 | platform-settings.backend.design.md#2.5.2 验收场景 | integration | 真实 PostgreSQL + 真实登录会话与 CSRF | TASK-009 | verified | ["uv","run","pytest","-q","tests/console_platform/test_auth_policy_settings.py"] | . | 300 | |
| E-16 | platform-settings.backend.design.md#2.5.2 验收场景 | integration | 真实源码树 + 真实 CSV + 真实机检（无服务） | TASK-016 | planned | ["uv","run","pytest","-q","tests/test_configuration_inventory.py"] | . | 300 | |
| E-17 | platform-settings.backend.design.md#2.5.2 验收场景 | integration | 真实 PostgreSQL（真实幂等表与 partial unique）+ 真实 HTTP | TASK-004 | verified | ["uv","run","pytest","-q","tests/console_platform/test_platform_settings_api.py","-k","idempotent_replay"] | . | 300 | |
| E-18 | platform-settings.backend.design.md#2.5.2 验收场景 | integration | 真实任务文档与 manifest（收口清单交叉核对） | TASK-012 | planned | ["uv","run","pytest","-q","tests/platform_settings_inventory.py"] | . | 300 | |
| E-19 | platform-settings.backend.design.md#2.5.2 验收场景 | integration | 真实 PostgreSQL + 真实 Worker 应用层（真实 lease/claim 语义） | TASK-006 | verified | ["uv","run","pytest","-q","tests/agent_worker/test_task_defaults_from_settings.py","-k","new_task_defaults"] | . | 600 | |
| E-20 | platform-settings.backend.design.md#2.5.2 验收场景 | integration | 真实 PostgreSQL + 真实 Runtime 装配 + 真实 Console 清理入口 | TASK-013 | verified | ["uv","run","pytest","-q","tests/agent_runtime/test_execution_defaults_settings.py"] | . | 600 | |
| E-21 | platform-settings.backend.design.md#2.5.2 验收场景 | integration | 真实 acceptance 栈（真实 Console API + 真实 PostgreSQL + 真实 Worker 进程） | TASK-014 | verified | ["uv","run","pytest","-q","tests/acceptance/dfx"] | . | 1200 | |
| E-22 | platform-settings.backend.design.md#2.5.2 验收场景 | integration | 真实 Console API（真实 HTTP PUT）+ 真实 PostgreSQL | TASK-015 | verified | ["uv","run","pytest","-q","tests/console_platform/test_platform_settings_api.py","-k","business_key_boundary"] | . | 300 | |
| E-23 | platform-settings.backend.design.md#2.5.2 验收场景 | integration | 真实 PostgreSQL + 真实登录会话与 CSRF | TASK-017 | verified | ["uv","run","pytest","-q","tests/acceptance/console_auth_flow/test_auth_acceptance.py"] | . | 600 | |
| B-01 | platform-settings.backend.design.md#2.5.2 验收场景 | unit | 真实设置文档 schema 函数（无服务） | TASK-001 | verified | ["uv","run","pytest","-q","tests/test_platform_settings_schema.py"] | . | 300 | |
| B-02 | platform-settings.backend.design.md#2.5.2 验收场景 | unit | 真实预算层级解析函数（无服务） | TASK-008 | verified | ["uv","run","pytest","-q","tests/agent_runtime/test_model_budget_layers.py"] | . | 300 | |
| B-03 | platform-settings.backend.design.md#2.5.2 验收场景 | unit | 真实 Gateway 回复生命周期取值函数（无服务） | TASK-007 | verified | ["uv","run","pytest","-q","tests/gateway/test_progress_settings.py"] | . | 300 | |
| B-04 | platform-settings.backend.design.md#2.5.2 验收场景 | integration | 真实 PostgreSQL（表内无该租户行） | TASK-003 | verified | ["uv","run","pytest","-q","tests/console_platform/test_platform_settings_service.py","-k","default_when_absent"] | . | 300 | |
| B-05 | platform-settings.frontend.design.md#2.4 验收条件 | E2E | 真实浏览器 + 真实路由 | TASK-010 | e2e_deferred | - | . | 600 | |
| B-06 | platform-settings.frontend.design.md#2.4 验收条件 | unit | 真实源码树 + 真实词条文件（无服务） | TASK-010 | verified | ["uv","run","pytest","-q","tests/frontend/test_platform_settings_contract.py","-k","applies_to_labels"] | . | 300 | |
| B-07 | platform-settings.backend.design.md#2.5.2 验收场景 | integration | 真实 PostgreSQL（真实 `alembic upgrade 0001→0017` / `downgrade`） | TASK-002 | verified | ["uv","run","pytest","-q","tests/console_platform/test_platform_settings_table.py"] | . | 300 | |
| B-08 | platform-settings.frontend.design.md#2.4 验收条件 | unit | 真实源码树（无服务） | TASK-010 | verified | ["uv","run","pytest","-q","tests/frontend/test_console_shell_contract.py","tests/frontend/test_platform_settings_contract.py"] | . | 300 | |

> 本表覆盖两份 design 的全部 **34** 条场景（S-01..S-03、E-01..E-23、B-01..B-08）。每条场景有且只有一个最终负责人；`E2E` 类（S-01..S-03、E-11..E-14、B-05）在编码期只登记，统一留给需求级 `verify-e2e`。

---

## TASK-001: 平台设置文档 schema 与联动校验

- **Status**: done
- **Priority**: P0
- **Depends**:
- **Source**: platform-settings.backend.design.md#2.3 功能方案, platform-settings.backend.design.md#3.1 方案选型
- **Spec-Refs**: harness-time#RULE-time-001, harness-model#RULE-model-001
- **Acceptance-Refs**: B-01

### Description

新增 `packages/contracts/src/muad_contracts/platform_settings.py`：整份平台设置文档（9 个分组、41 个叶子）的类型、默认值、解析与**跨字段联动校验**的唯一来源。按 ADR-10，把压缩分组 schema 从 `muad_agent_core.context.settings` **整体迁移**过来（原文件删除，11 处导入改指向新模块，**不留 re-export 薄壳**）。

### Checklist

- [x] 新建 `muad_contracts/platform_settings.py`：9 个分组的 dataclass/pydantic 模型 + `default_platform_settings()` + `parse_platform_settings()` + `validate_platform_settings()`，字段与默认值严格对齐 design §2.3.2 的 41 行表
- [x] 迁移 `muad_agent_core/context/settings.py` 的压缩分组定义与 `_validate` 到新模块；**删除原文件**，更新 11 处导入（`apps/agent-runtime` 6 处 + `tests` 5 处）
- [x] 联动校验：`snip.max_groups ≥ keep_head + keep_tail + 1`；`preview_head + preview_tail ≤ round_budget_bytes`；`summary.enabled` 需有效 `model_ref`；`batch_max_concurrency ≤ batch_platform_limit`（环境项）；`recall_default_limit ≤ RECALL_MAX_LIMIT`；**未知键一律拒绝**（fail-closed）
- [x] `locale.default_timezone` 只接受 IANA 时区（`Asia/Shanghai` 一类）；`locale.default_locale` 只接受 `zh-CN`/`en-US`
- [x] `compaction.summary.model_ref` 只作**主键引用**语义校验，不引入平台默认模型（`RULE-model-001`）
- [x] 敏感键（`password`/`secret`/`token`/`api_key`/`dsn`/`credential` 命名）在白名单之外 ⇒ 天然被拒；补充显式断言
- [x] [B-01][unit] 覆盖各叶子范围边界（下界-1/下界/上界/上界+1/未知键）与全部联动组合；真实边界：**真实 schema 函数，不 mock**（参考 `tests/test_settings.py` 的直调风格）
- [x] 先写测试并记录 RED（新模块不存在 ⇒ 收集失败），再实现
- [x] 同步 `docs/configuration-inventory.csv` 与 `tests/test_configuration_inventory.py` 的期望（`settings.py` 路径/行号迁移）
- [x] verifier `harness-time#RULE-time-001`：`uv run pytest -q tests/frontend/test_datetime_contract.py && uv run pytest -q tests -k schema_parity`（真实边界：真实前端契约源 + 真实迁移链；IANA 时区口径不得回退）
- [x] verifier `harness-model#RULE-model-001`：`uv run pytest -q tests/console_platform/test_models_api.py && uv run pytest -q tests/console_platform/test_agents_api.py -k disabled`（真实边界：真实 PG + 真实 Console API；不引入平台默认模型）
- [x] 运行验收命令并填写 Acceptance Evidence

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|--------|---------|--------------------|---------|----------------|---------|------|
| B-01 | unit | 真实设置文档 schema 函数（无服务） | 界内接受/界外拒绝；错误定位到字段路径；未知键拒绝；五类联动组合逐一验证；IANA 时区与 locale 枚举 | `tests/test_platform_settings_schema.py` | ["uv","run","pytest","-q","tests/test_platform_settings_schema.py"] | verified |

### Acceptance Evidence

> 编码期记录 functional 的 RED/GREEN、断言位置与真实组件证据；本任务无 E2E 场景。

**RED**（先写测试、后实现；新模块不存在）：`uv run pytest -q tests/test_platform_settings_schema.py` 收集期失败 ——
`ModuleNotFoundError: No module named 'muad_contracts.platform_settings'`（`Interrupted: 1 error`）。

**GREEN**（★ = 复核人在提交后独立复跑）：
- ★ `uv run pytest -q tests/test_platform_settings_schema.py` → **105 passed**（0.05s）
- `uv run pytest -q tests/agent_core tests/agent_runtime -k "compaction or context"` → **84 passed, 370 deselected**
- ★ `uv run pytest -q tests/test_configuration_inventory.py` → **3 passed**（0.71s，CSV 已随模块搬迁同步）
- `uv run pytest -q tests/test_contracts.py` → 14 passed

| 断言 | 位置 | 真实边界证据 |
|------|------|-------------|
| 9 分组 / 41 叶子的字段、类型、默认值 | `tests/test_platform_settings_schema.py` | 真实 `muad_contracts.platform_settings`（无 mock、无服务） |
| 各叶子范围边界（下界-1 / 下界 / 上界 / 上界+1）与**未知键拒绝** | 同上 | 直调 `validate_platform_settings()`，fail-closed |
| 联动校验：`max_groups ≥ head+tail+1`、`preview_head+preview_tail ≤ round_budget_bytes`、`summary.enabled` 需 `model_ref`、`budget_ratio ∈ [0,1]`、上界注入（`batch_platform_limit`/`RECALL_MAX_LIMIT`） | 同上 | 同上（上界由调用方注入，contracts 不反向依赖 app/env） |
| `locale.default_timezone` 只收 IANA 时区、`default_locale` 只收 `zh-CN`/`en-US` | 同上 | 真实 `zoneinfo.ZoneInfo` 校验 |
| 敏感键（`password`/`secret`/`token`/`api_key`/`dsn`/`credential`）被拒 | 同上 | 白名单之外的命名一律拒绝 |
| 压缩分组契约不回归（搬迁后） | `tests/agent_core` + `tests/agent_runtime -k "compaction or context"` | 真实 Runtime 装配（真实 PostgreSQL） |

**required Rule verifier（实跑）**：
- `harness-time#RULE-time-001`：`uv run pytest -q tests/frontend/test_datetime_contract.py`（2 passed）`&& uv run pytest -q tests -k schema_parity`（35 passed）
- `harness-model#RULE-model-001`：`uv run pytest -q tests/console_platform/test_models_api.py`（8 passed）`&& uv run pytest -q tests/console_platform/test_agents_api.py -k disabled`（1 passed, 10 deselected）

**迁移范围与复核**：`muad_agent_core/context/settings.py`（253 行）整体搬迁为 `muad_contracts/platform_settings.py`（530 行），原文件删除；导入点 **13 处**（设计预估 11 处 —— `context/compactor.py` 的相对导入 `from .settings` 是普通 grep 漏掉的第 12/13 处）。复核 `grep -rn "muad_agent_core.context.settings"` 仅剩新模块 docstring 的一处说明，**无 re-export 薄壳**。

- B-01: verified — automated command passed; run_id=34b14f1e97944e019c2fc4e5e556cdf5 (confirmed_by: runner)

### Log
- [2026-10-05] created (draft)
- [2026-10-05] started
- [2026-10-05] completed (done)

---

## TASK-002: 平台设置表与迁移

- **Status**: done
- **Priority**: P0
- **Depends**:
- **Source**: platform-settings.backend.design.md#3.3 数据设计, platform-settings.backend.design.md#4.4 数据迁移
- **Spec-Refs**: harness-data#RULE-data-001
- **Acceptance-Refs**: B-07

### Description

新增 `control.platform_setting` 的 append-only 版本表与迁移 `0017_platform_setting`，以及对应的 SQLAlchemy model。当前版本 = 该租户最大 `revision`；乐观并发由 partial unique 兜底（ADR-02）。

### Checklist

- [x] 迁移 `migrations/versions/0017_platform_setting.py`（`revision="0017"`, `down_revision="0016"`）：建表 + `uq_platform_setting_tenant_revision`（partial unique `(tenant_id, revision) WHERE is_deleted = false`）+ `ix_platform_setting_tenant_revision_desc`；`downgrade` 干净删表
- [x] Console model 落在 `infrastructure/models/control.py`（`{"schema": "control"}`），列与 design §3.3 表一致（`id/tenant_id/revision/settings_json/actor_user_id/is_deleted/create_time/update_time`）
- [x] 登记进 `tests/console_platform/test_schema_parity.py` 的 `EXPECTED_INDEXES`（新增表必须纳入对等校验）
- [x] [B-07][integration] 真实 PG：`alembic upgrade 0001→0017` 后列/索引与 model 一致；重复 `(tenant_id, revision)` 插入被 partial unique 拒绝；`downgrade` 可回滚；真实边界：**真实 PostgreSQL + 真实 alembic**（不 mock）
- [x] 先跑一次 RED（表不存在 ⇒ 用例失败），再实现
- [x] verifier `harness-data#RULE-data-001`：`uv run pytest -q tests -k schema_parity`（真实边界：真实 PostgreSQL 迁移链 + 真实 model 定义）
- [x] 运行验收命令并填写 Acceptance Evidence

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|--------|---------|--------------------|---------|----------------|---------|------|
| B-07 | integration | 真实 PostgreSQL（真实 `alembic upgrade 0001→0017` / `downgrade`） | 列与索引与 model 定义一致；partial unique 真实拒绝重复 `(tenant_id, revision)`；`downgrade` 干净回滚 | `tests/console_platform/test_platform_settings_table.py` | ["uv","run","pytest","-q","tests/console_platform/test_platform_settings_table.py"] | verified |

### Acceptance Evidence

> 编码期填写 RED/GREEN 与断言位置；E2E 场景只登记。

**RED**（先写测试、后实现；model 尚未落地，收集期即失败）：
`uv run pytest -q tests/console_platform/test_platform_settings_table.py` ——
```
ImportError while importing test module 'tests/console_platform/test_platform_settings_table.py'
E   ImportError: cannot import name 'PlatformSetting' from 'muad_console_platform.infrastructure.models.control'
!!!!!!!!!!!!!!!!!!!!!!!!!!! Interrupted: 1 error during collection !!!!!!!!!!!!!!!!!!!!!!!!!!!
1 error in 0.07s
```

**GREEN**（★ = required verifier）：
- ★ `harness-data#RULE-data-001`：`uv run pytest -q tests -k schema_parity` → **36 passed, 2230 deselected**（5.03s；TASK-001 基线 35，+1 即本需求新增的 `platform_setting` 对等用例）
- B-07 验收命令：`uv run pytest -q tests/console_platform/test_platform_settings_table.py` → **3 passed**（1.70s）
- `uv run pytest -q tests/console_platform/test_schema_parity.py` → **8 passed**（0.13s）
- `uv run pytest -q tests/acceptance/test_foundation_schema_parity.py` → **3 passed**（2.93s，含单头校验与 `0001→0017` 链完整、注入列漂移可被检出）
- `uv run ruff check migrations/versions/0017_platform_setting.py apps/console-platform/backend/src/muad_console_platform/infrastructure/models/control.py tests/console_platform/test_platform_settings_table.py tests/console_platform/test_schema_parity.py` → All checks passed
- `uv run mypy apps/console-platform/backend/src/muad_console_platform/infrastructure/models/control.py` → Success: no issues found

| 断言 | 位置 | 真实边界证据 |
|------|------|-------------|
| `alembic upgrade 0001→0017` 后的列集合 == 设计 §3.3 的 8 列、主键 == `id`、无物理 FK | `test_upgrade_to_head_creates_table_matching_orm_model` | 真实 PostgreSQL 反射（`inspect.get_columns/get_pk_constraint/get_foreign_keys`） |
| 列可空性 / 索引名 / 唯一性 / partial 谓词 / 索引列与 ORM model 逐项一致（复用 `test_schema_parity.py` 的 `_compare`，两处不会漂移） | 同上 | 真实 model 定义 `control.PlatformSetting` vs 真实 DB |
| 同 `(tenant_id, revision)` 的第二行被 partial unique 真实拒绝（`IntegrityError`）；软删后同一键可再插 ⇒ 谓词确实是 partial | `test_partial_unique_rejects_duplicate_tenant_revision` | 真实 PostgreSQL 唯一约束（`uq_platform_setting_tenant_revision`） |
| `downgrade 0016` 干净删表（`alembic_version=0016` 且表消失）、`upgrade 0017` 重新建出同形表 | `test_downgrade_drops_table_and_upgrade_restores` | 真实 alembic 子进程往返 |

**迁移往返用的库（隔离红线）**：`downgrade` **只在** `tests/acceptance/datastores.create_datastore()` 建出的临时库 `muad_pst_<uuid>`（空库 + 迁到 head；跑完 `DROP DATABASE ... WITH (FORCE)` 并归还 Redis 号位）上执行；往返用的 alembic ini 由用例自建、只含该临时 DSN（`migrations/` 只读 ini、不读环境变量）。共享 dev 库 `muad` 全程**只做过 `upgrade head`（0016→0017），未做任何 downgrade**；跑完确认无 `muad_pst_*` 残留库。
- B-07: verified — automated command passed; run_id=de9a893dc3a14e9e864062e7f12c588d (confirmed_by: runner)

### Log
- [2026-10-05] created (draft)
- [2026-10-05] started
- [2026-10-05] completed (done)

---

## TASK-003: 设置 service：读取、保存、版本与审计

- **Status**: done
- **Priority**: P0
- **Depends**: TASK-001, TASK-002
- **Source**: platform-settings.backend.design.md#3.4 接口设计, platform-settings.backend.design.md#3.5 质量实现方案
- **Spec-Refs**:
- **Acceptance-Refs**: B-04, E-02, E-05

### Description

`PlatformSettingsService`：读当前版本（无记录回落 schema 默认）、保存（校验 → 事务内比对 revision → `INSERT revision+1` → 同事务写审计）、按版本读、回滚（生成新版本）。乐观并发靠 partial unique 兜底，不用行锁。

### Checklist

- [x] `application/platform_settings_service.py`：`read_current(tenant_id)`、`save(tenant_id, actor, revision, settings)`、`list_revisions(...)`、`read_revision(...)`、`restore(...)`
- [x] 读路径：无版本行 ⇒ 返回 `revision=0` + `default_platform_settings()`，不报错
- [x] 保存：先 `validate_platform_settings()`（复用 TASK-001），再事务内比对 `max(revision)`；不等 ⇒ `PLATFORM_SETTINGS_VERSION_CONFLICT`；`INSERT` 撞唯一约束同样归为冲突
- [x] 审计与业务**同一事务**：`AuditService.record_config_change`（`resource_type="PLATFORM_SETTING"`, `action ∈ {CREATE, UPDATE, RESTORE}`），失败一起回滚
- [x] `resource_type="PLATFORM_SETTING"` 写进 config 审计；**不要**动 `AUDIT_TYPES`（那是审计来源枚举，不是 `resource_type` 注册表，见 design v0.4）；按 `tests/frontend/test_audit_gap_contract.py` 的机检同步**前端登记域**：`AuditFilterBar.tsx` 的 `RESOURCE_TYPES` + zh-CN/en-US 的 `audit.resourceType.PLATFORM_SETTING` 词条，并跑 `uv run pytest -q tests/frontend/test_audit_gap_contract.py tests/frontend/test_audit_i18n_contract.py` 确认非红
- [x] [B-04][integration] 覆盖无版本行回落 schema 默认；真实边界：**真实 PostgreSQL**（不 mock session）
- [x] [E-02][integration] 覆盖真实并发保存：同 revision 两次提交，一次成功一次冲突，**不产生第二个版本**；真实边界：**真实 PostgreSQL 唯一约束**
- [x] [E-05][integration] 覆盖审计同事务落库（actor/revision/before/after 脱敏）与「审计写失败 ⇒ 保存一起回滚」
- [x] 先写测试并记录 RED，再实现
- [x] 运行验收命令并填写 Acceptance Evidence

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|--------|---------|--------------------|---------|----------------|---------|------|
| B-04 | integration | 真实 PostgreSQL（表内无该租户行） | `revision=0` + schema 默认；不抛错；首次保存创建 revision 1 | `tests/console_platform/test_platform_settings_service.py -k default_when_absent` | ["uv","run","pytest","-q","tests/console_platform/test_platform_settings_service.py","-k","default_when_absent"] | verified |
| E-02 | integration | 真实 PostgreSQL（真实唯一约束） | 并发同 revision ⇒ 一成功一冲突；版本总数只 +1 | `tests/console_platform/test_platform_settings_service.py -k version_conflict` | ["uv","run","pytest","-q","tests/console_platform/test_platform_settings_service.py","-k","version_conflict"] | verified |
| E-05 | integration | 真实 PostgreSQL（同一事务） | `config_audit_log` 同事务出现记录；审计失败 ⇒ 保存回滚 | `tests/console_platform/test_platform_settings_service.py -k audit_same_transaction` | ["uv","run","pytest","-q","tests/console_platform/test_platform_settings_service.py","-k","audit_same_transaction"] | verified |

### Acceptance Evidence

> 编码期记录 functional 的 RED/GREEN、断言位置与真实边界证据；本任务无 E2E 场景。

**RED**（先写测试、后实现；服务模块不存在，收集期即失败）：
`uv run pytest -q tests/console_platform/test_platform_settings_service.py` ——
```
ImportError while importing test module '.../tests/console_platform/test_platform_settings_service.py'.
E   ModuleNotFoundError: No module named 'muad_console_platform.application.platform_settings_service'
!!!!!!!!!!!!!!!!!!!!!!!!!!! Interrupted: 1 error during collection !!!!!!!!!!!!!!!!!!!!!!!!!!!
1 error in 0.08s
```

**GREEN**（★ = 复核人可独立复跑）：
- ★ B-04：`uv run pytest -q tests/console_platform/test_platform_settings_service.py -k default_when_absent` → **1 passed, 7 deselected**
- ★ E-02：`uv run pytest -q tests/console_platform/test_platform_settings_service.py -k version_conflict` → **2 passed, 6 deselected**
- ★ E-05：`uv run pytest -q tests/console_platform/test_platform_settings_service.py -k audit_same_transaction` → **2 passed, 6 deselected**
- 全文件：`uv run pytest -q tests/console_platform/test_platform_settings_service.py` → **8 passed**（0.69s）
- 不回归：`uv run pytest -q tests/console_platform/test_platform_settings_table.py` → **3 passed**；`uv run pytest -q tests/console_platform -k audit` → **34 passed, 128 deselected**；`uv run pytest -q tests/console_platform` → **162 passed**（21.98s）
- 前端登记域机检：`uv run pytest -q tests/frontend/test_audit_gap_contract.py tests/frontend/test_audit_i18n_contract.py` → **16 passed**（值域派生已含 `PLATFORM_SETTING`：`missing=[] extra=[]`）
- 静态：`uv run ruff check platform_settings_service.py test_platform_settings_service.py` → All checks passed；`uv run mypy platform_settings_service.py` → Success: no issues found；`uv run pytest -q tests/test_error_catalog.py` → 10 passed

| 断言 | 位置 | 真实边界证据 |
|------|------|-------------|
| 无版本行 ⇒ `revision=0` + schema 默认、不抛错、不落行；首次保存建立 revision 1 | `test_read_current_default_when_absent_then_first_save_creates_revision_one` | 真实 PostgreSQL：读后 `SELECT revision` 为空；保存后仅 `[1]` |
| 同 revision 第二次提交 ⇒ 冲突、版本总数只 +1 | `test_version_conflict_stale_revision_is_rejected_without_second_version` | 真实 PostgreSQL：事务内 `max(revision)` 比对；`revision` 仍为 `[1]` |
| 真实并发：两事务同 revision、落败者撞 partial unique ⇒ 归一为版本冲突 | `test_version_conflict_concurrent_save_loses_on_partial_unique` | 真实唯一索引 `uq_platform_setting_tenant_revision`：第一事务 flush 未提交时，第二事务卡在 `INSERT`（`assert not pending.done()`），提交后抛 `IntegrityError` 被归一；版本仍为 `[1]` |
| 审计同事务落库：CREATE/UPDATE、actor、before/after 为脱敏后的设置文档 | `test_audit_same_transaction_records_create_then_update_with_before_after` | 真实 `control.config_audit_log`（真实 `write_config_audit` + `sanitize_audit_payload`）：action/actor/before/after 逐项比对，且含完整归一化文档 |
| 审计写失败 ⇒ 已 flush 的版本行一并回滚 | `test_audit_same_transaction_write_failure_rolls_back_the_save` | 真实事务：失败时同 session 可见已 flush 的版本行（`staged == 1`），回滚后版本行与审计行均为空（注入的是 `write_config_audit` 抛错，业务路径不 mock） |
| 版本历史倒序分页带相邻版本 `changed_keys`；`read_revision` 返回目标版本、缺失抛 `PLATFORM_SETTINGS_REVISION_NOT_FOUND` | `test_list_revisions_descending_with_changed_keys_and_read_revision` | 真实 PostgreSQL：`[2, 1]` 倒序、`task.max_attempts` 命中 diff |
| 回滚生成**新** revision（内容等于目标版本）、写 RESTORE 审计、历史行全保留 | `test_restore_appends_new_revision_and_keeps_history` | 真实 PostgreSQL：`[1, 2, 3]`，RESTORE 审计 before/after 内容正确 |
| 历史内容不合现行 schema ⇒ 拒绝回滚并说明原因，不产生新版本 | `test_restore_rejects_history_that_fails_current_schema` | 直接植入"旧 schema"行后 `restore` 抛 `PlatformSettingsError`（`未知分组`），版本仍为 `[1]` |

**审计登记域（与 design v0.4 对齐）**：`resource_type="PLATFORM_SETTING"` 由模块常量
`AUDIT_RESOURCE_TYPE`（`AUDIT_` 前缀供机检派生）写入 `config_audit_log`；**未**改动
`AUDIT_TYPES`（它是审计来源枚举，非 `resource_type` 注册表）。真正需同步的是前端登记域，
本任务已改且仅改这三处：`AuditFilterBar.tsx` 的 `RESOURCE_TYPES`、`locales/zh-CN.json`
与 `locales/en-US.json` 的 `audit.resourceType.PLATFORM_SETTING`。

**任务文档修正**：TASK-003 的 `- **Spec-Refs**: N/A` 改为**空**（本任务无 required Rule）。
`cf_spec_context.py` 的 `_refs` 只按逗号切分，`N/A` 会被当成未知规则 ref ⇒ `unknown_spec_refs`；
本仓 archived 任务（如 context-compaction、attachment-round-trip）均用空值表达"无 required
Rule"。同文件 TASK-008/011/013 仍是 `N/A`，属各自任务的范围，未在本任务改动。
- E-02: verified — automated command passed; run_id=0abc5bfd2e7d4e54a84e52fdfa30b5d8 (confirmed_by: runner)
- E-05: verified — automated command passed; run_id=0abc5bfd2e7d4e54a84e52fdfa30b5d8 (confirmed_by: runner)
- B-04: verified — automated command passed; run_id=0abc5bfd2e7d4e54a84e52fdfa30b5d8 (confirmed_by: runner)

### Log
- [2026-10-05] created (draft)
- [2026-10-05] started
- [2026-10-05] completed (done)

---

## TASK-004: Console API 与内部取设置端点

- **Status**: done
- **Priority**: P0
- **Depends**: TASK-003
- **Source**: platform-settings.backend.design.md#3.4 接口设计
- **Spec-Refs**: harness-api#RULE-api-001, harness-api#RULE-api-002, harness-auth#RULE-auth-001, harness-secret#RULE-secret-001, harness-log#RULE-log-001
- **Acceptance-Refs**: E-01, E-04, E-06, E-10, E-17

### Description

六个端点：平台设置读（admin）、保存（admin + CSRF + 乐观并发 + `Idempotency-Key`）、版本历史（分页）、回滚（POST + `Idempotency-Key`）、已认证限额、内部取设置快照（服务身份 + `X-Tenant-Id`）。

### Checklist

- [x] `api/platform_settings.py`：API-01..API-05，按 `api/router.py` 分组注册（读/写/历史/回滚进 admin 组，限额进 authenticated 组）；租户一律 `AccountTenantId`
- [x] API-06 进 internal 组（`api/internal_platform_settings.py`），复用 `require_service_identity` + `HeaderTenantId`；失败即失败，**不返回最后一次已知值**
- [x] 统一封套与错误码（`config/api-messages.yaml`）：`VALIDATION_FAILED`（含 `details[].path`）、`PLATFORM_SETTINGS_VERSION_CONFLICT`、`PLATFORM_SETTINGS_SECRET_REJECTED`、`PLATFORM_SETTINGS_REVISION_NOT_FOUND`、`MODEL_NOT_FOUND`、`IDEMPOTENCY_MISMATCH`
- [x] 幂等（`RULE-api-002`）：保存与回滚均支持 `Idempotency-Key`，复用既有幂等表与规范化 JSON 指纹口径（**必须含 endpoint 与 tenant_id**），落败者读首次结果
- [x] 敏感键扫描与拒绝；响应体/错误体不回显任何凭据形状（`RULE-secret-001`）
- [x] 审计与日志走既有原语与脱敏（`RULE-log-001`）；变更值全文不入日志
- [x] 版本历史按 `{items,page,page_size,total}` 返回并带 `changed_keys`（相邻版本 diff）
- [x] 幂等需要落库时，按既有形态新建 `control.platform_setting_idempotency`（partial unique `(tenant_id, idempotency_key, endpoint)`）+ 迁移 `0018`，并登记进 `test_schema_parity.py`；指纹口径照 `channel_service.bind_fingerprint`（规范化 JSON 含 endpoint 与 tenant_id）
- [x] 按 design §3.5 实现两个计数指标并登记进 `metrics.py` 的 CATALOG：`platform_settings_save_total`（`result=ok|validation_failed|conflict`）、`platform_settings_fetch_total`（`caller=runtime|worker|gateway`, `result=ok|failed`）——后者是 TASK-005 的 E-03 断言依赖
- [x] [E-01][integration] 覆盖非法/越界/联动/未知键 ⇒ 400 + 字段路径，**不产生新版本**
- [x] [E-04][integration] 覆盖内部端点无/错服务身份、错租户 ⇒ 403，不泄漏内容
- [x] [E-06][integration] 覆盖回滚产生新版本、历史全保留、按当前 schema 校验旧内容
- [x] [E-10][integration] 覆盖敏感键拒绝 + 审计/响应体/日志无敏感值
- [x] [E-17][integration] 覆盖 `Idempotency-Key` 同指纹重放（revision 不变）与不同指纹 `IDEMPOTENCY_MISMATCH`
- [x] 先写测试并记录 RED，再实现
- [x] verifier `harness-api#RULE-api-001`：`uv run pytest -q tests/test_api_i18n.py tests/test_error_catalog.py tests/acceptance/test_foundation_api_envelope.py`（真实边界：真实 HTTP 封套与真实错误码目录）
- [x] verifier `harness-api#RULE-api-002`：`uv run pytest -q tests/console_skill/test_import_idempotency.py` ＋本任务新增的设置幂等用例（真实边界：真实 PG 幂等表 partial unique）
- [x] verifier `harness-auth#RULE-auth-001`：`uv run pytest -q tests/console_platform/test_user_side_relations.py -k s04 && uv run pytest -q tests -k schema_parity`（真实边界：真实授权关系表 + 真实登录会话）
- [x] verifier `harness-secret#RULE-secret-001`：`uv run pytest -q tests/test_logging_redaction.py tests/acceptance/test_foundation_ops_audit.py`（真实边界：真实日志与审计落库）
- [x] verifier `harness-log#RULE-log-001`：`uv run pytest -q tests/test_logging.py tests/test_logging_redaction.py tests/acceptance/test_foundation_logging.py`（真实边界：真实 logging-kit 输出）
- [x] 运行验收命令并填写 Acceptance Evidence

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|--------|---------|--------------------|---------|----------------|---------|------|
| E-01 | integration | 真实 PostgreSQL + 真实 settings service | 400 + `details[].path` 定位字段；版本数不变 | `tests/console_platform/test_platform_settings_api.py -k invalid_payload` | ["uv","run","pytest","-q","tests/console_platform/test_platform_settings_api.py","-k","invalid_payload"] | verified |
| E-04 | integration | 真实 Console API（真实服务身份校验） | 无/错 `X-Internal-Service` ⇒ 403；`X-Tenant-Id` 非本租户 ⇒ 无数据 | `tests/console_internal/test_platform_settings_internal.py -k service_identity` | ["uv","run","pytest","-q","tests/console_internal/test_platform_settings_internal.py","-k","service_identity"] | verified |
| E-06 | integration | 真实 PostgreSQL | 回滚产生新 revision（内容等于目标版本）；历史行全在；旧 schema 内容被拒 | `tests/console_platform/test_platform_settings_api.py -k restore` | ["uv","run","pytest","-q","tests/console_platform/test_platform_settings_api.py","-k","restore"] | verified |
| E-10 | integration | 真实 PostgreSQL + 真实 HTTP 响应体 | 敏感键被拒；审计行/响应体/日志无敏感值 | `tests/console_platform/test_platform_settings_api.py -k secret_rejected` | ["uv","run","pytest","-q","tests/console_platform/test_platform_settings_api.py","-k","secret_rejected"] | verified |
| E-17 | integration | 真实 PostgreSQL（真实幂等表与 partial unique）+ 真实 HTTP | 同键同指纹重放首次结果（revision 不变）；同键不同指纹 ⇒ `IDEMPOTENCY_MISMATCH` | `tests/console_platform/test_platform_settings_api.py -k idempotent_replay` | ["uv","run","pytest","-q","tests/console_platform/test_platform_settings_api.py","-k","idempotent_replay"] | verified |

### Acceptance Evidence

**RED（真实失败原文；迁移 0018 应用前，dev 库 head=0017）**

```
$ uv run pytest -q tests/console_platform/test_platform_settings_api.py -k idempotent_replay
E   sqlalchemy.exc.ProgrammingError: ... asyncpg.exceptions.UndefinedTableError:
    relation "control.platform_setting_idempotency" does not exist
    [SQL: SELECT control.platform_setting_idempotency.tenant_id, ... ]
1 failed, 3 deselected, 1 error in 0.75s
```

同一次运行（`-k "invalid_payload or restore or secret_rejected"`）在 teardown 卸表时同样撞
`UndefinedTableError`（`1 failed, 2 passed, 1 deselected, 3 errors`）——证明五条用例确实压在新表上。
`tests/console_internal/test_platform_settings_internal.py` 首次运行返回 403（autouse 的
`internal_service_token` 夹具未随 `service_headers` 一起 import 注册），补齐 import 后转绿。
应用 `uv run alembic -c migrations/alembic.ini upgrade head`（→0018）后全部转 GREEN。

**GREEN（五条验收命令逐条单跑，真实 PostgreSQL + 真实 HTTP）**

| 场景 | 命令 | 结果 |
|------|------|------|
| E-01 | `uv run pytest -q tests/console_platform/test_platform_settings_api.py -k invalid_payload` | `1 passed` |
| E-04 | `uv run pytest -q tests/console_internal/test_platform_settings_internal.py -k service_identity` | `1 passed` |
| E-06 | `uv run pytest -q tests/console_platform/test_platform_settings_api.py -k restore` | `1 passed` |
| E-10 | `uv run pytest -q tests/console_platform/test_platform_settings_api.py -k secret_rejected` | `1 passed` |
| E-17 | `uv run pytest -q tests/console_platform/test_platform_settings_api.py -k idempotent_replay` | `1 passed` |

非回归与配套：`tests/console_platform` → **168 passed**；`tests/console_internal` → **28 passed**；
`tests/test_error_catalog.py tests/test_api_i18n.py` → **15 passed**；`test_schema_parity.py` → **9 passed**；
`tests/test_platform_settings_table.py`（升级 head 断言改为 0018 后）随套件通过。

verifier：RULE-api-001 → **18 passed**（api_i18n + error_catalog + foundation_api_envelope）；
RULE-api-002 → **5 passed**（skill import 幂等）+ 本文件 `-k idempotent_replay`；
RULE-auth-001 → `test_user_side_relations -k s04` **2 passed** + 全仓 `-k schema_parity` **37 passed**；
RULE-secret-001 → **14 passed**；RULE-log-001 → **13 passed**。

**关键断言位置**

- E-01：`test_platform_settings_api.py:110-115`（400 + `data.details[0].path` ∈ {task.max_attempts, snip.max_groups, task.batch_max_concurrency, task.nope, bogus, snip.enabled}；`_revisions == []`）。
- E-04：`test_platform_settings_internal.py:55-77`（无/错身份 403 FORBIDDEN 且 `data is None`；本租户 revision=1/值 9；错租户 revision=0 且回落 schema 默认 `max_attempts==3`）。
- E-06：`test_platform_settings_api.py:131-158`（restore 返回 `{revision:3, restored_from:1}`；历史 `[1,2,3]` 全在；当前值=7；缺版本 404 `PLATFORM_SETTINGS_REVISION_NOT_FOUND`；注入旧 schema 行后回滚 400 且路径 `unknown_group`、历史 `[1,2,3,4]` 不变）。
- E-10：`test_platform_settings_api.py:169-183`（400 `PLATFORM_SETTINGS_SECRET_REJECTED`；响应体/审计 JSON/`caplog` 均不含敏感值 `sk-LIVE-SECRET-6f2b`；被拒不得落版本/审计）。
- E-17：`test_platform_settings_api.py:213-262`（同键同指纹重放返回首次结果且 `_revisions==[1]`；异指纹 409 `IDEMPOTENCY_MISMATCH`；同 `(tenant,key,endpoint)` 直插第二行抛 `IntegrityError`＝真实 partial unique；回滚幂等同口径）。

**真实边界证据**

- **真实 PostgreSQL**：版本表 `control.platform_setting` 与幂等表 `control.platform_setting_idempotency`
  都是真实行；幂等 partial unique 由真实索引兜底（用例里直插重复 `(tenant_id,idempotency_key,endpoint)`
  触发 `IntegrityError`）；回滚与审计同事务由 TASK-003 的 service 复用。
- **真实 Console API**：五条用例都经 `ASGITransport(app=app)` 走真实路由与真实封套，未见 mock。
- **真实服务身份**：API-06 用真实 `require_service_identity`（`X-Internal-Service`）与 `HeaderTenantId`；
  无/错身份 403、错租户读不到本租户内容。
- **迁移**：新建 `0018_platform_setting_idempotency`（`down_revision="0017"`），dev 库升级到 0018 建表；
  `tests/console_platform/test_platform_settings_table.py` 的 head 断言随之改为 0018（表自身语义不变）。
- **指标**：`platform_settings_save_total{result=ok|validation_failed|conflict}` 与
  `platform_settings_fetch_total{caller,result}` 登记进 `metrics.py` 的 CATALOG（安装时 `declare_metric`）。
- E-01: verified — automated command passed; run_id=01cb35ccb7ab48539e2a60a61d7080aa (confirmed_by: runner)
- E-04: verified — automated command passed; run_id=01cb35ccb7ab48539e2a60a61d7080aa (confirmed_by: runner)
- E-06: verified — automated command passed; run_id=01cb35ccb7ab48539e2a60a61d7080aa (confirmed_by: runner)
- E-10: verified — automated command passed; run_id=01cb35ccb7ab48539e2a60a61d7080aa (confirmed_by: runner)
- E-17: verified — automated command passed; run_id=01cb35ccb7ab48539e2a60a61d7080aa (confirmed_by: runner)

### Log
- [2026-10-05] created (draft)
- [2026-10-05] started
- [2026-10-05] completed (done)

---

## TASK-005: Runtime 平台设置读取缝与冻结

- **Status**: done
- **Priority**: P0
- **Depends**: TASK-004
- **Source**: platform-settings.backend.design.md#3.1 方案选型, platform-settings.backend.design.md#3.2 架构设计
- **Spec-Refs**: harness-snapshot#RULE-snapshot-001, harness-arch#RULE-arch-001
- **Acceptance-Refs**: E-03, E-07, E-08

### Description

删除 `ContextSettingsCache` 的 TTL 语义与 `context_settings_cache_ttl_sec`（ADR-03）：平台设置改为在 Run 创建事务内**取一次**（内部端点），显式传入 `resolve_compaction_settings(platform_overrides=…)`，结果立即冻结进 `policy_json`。`DEFAULT_POLICY` 不再当动态默认源。

### Checklist

- [x] 新增取快照 client（内部 HTTP，复用 `ConsoleResolveClient` 的 `X-Internal-Service` 口径与超时常量风格）——按仓库分层落在 `infrastructure/platform_settings_client.py`（见 Evidence「落地位置」）
- [x] client 调用带 `X-Caller-Service: runtime`，并在**客户端侧**记录 `platform_settings_fetch_total{caller="runtime", result="failed"}`——端点被切断时 Console 收不到请求，失败计数只能由这里记（design v0.5，E-03 的断言依赖它）
- [x] 删除 `application/context_settings.py` 的进程级 TTL 单例与 `_default_cache`；`_create_run` 改为先取快照再解析，压缩配置随 `snapshot_policy()` 冻结（解析一次、冻结值与实际用值不分叉）
- [x] `SharedSettings.context_settings_cache_ttl_sec` 删除（`packages/common/src/muad_common/settings.py`），`.env.example` 同步移除（该键本就不在 `.env.example`，无需删除）
- [x] `DEFAULT_POLICY` 的用途改为「无平台设置时的 schema 默认」，不得作为运行期动态默认源
- [x] 取设置**只**发生在 Run 创建边界：断言每 Run 恰好一次，不进入每轮模型调用/工具执行
- [x] [E-03][integration] 覆盖设置源不可读 ⇒ Run 创建明确失败 + 失败指标，**不回退过期默认值**
- [x] [E-07][integration] 覆盖 Agent 显式 `runtime_config.budget.compaction` 覆盖平台默认；读接口如实返回覆盖数量
- [x] [E-08][integration] 覆盖 Console 保存后**独立 Runtime 进程**的新 Run 读到新 revision（无 TTL、无重启）
- [x] 先写测试并记录 RED，再实现
- [x] 同步 `docs/configuration-inventory.csv` 与机检期望（`context_settings_cache_ttl_sec` 移除、`context_settings.py` 变更）
- [x] verifier `harness-snapshot#RULE-snapshot-001`：`uv run pytest -q tests/agent_runtime/test_snapshot_freeze.py tests/agent_runtime/test_run_reaper.py && uv run pytest -q tests/agent_runtime -k "executor or resolve"`（真实边界：真实 PG 冻结行 + 真实执行器装配）
- [x] verifier `harness-arch#RULE-arch-001`：`uv run pytest -q tests/architecture`（真实边界：真实源码树架构断言）
- [x] 运行验收命令并填写 Acceptance Evidence

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|--------|---------|--------------------|---------|----------------|---------|------|
| E-03 | integration | 真实 Runtime 进程 + 被切断的 Console 内部端点 | Run 创建明确失败（统一错误码）；指标 `platform_settings_fetch_total{result="failed"}` 递增；无过期默认值参与装配 | `tests/agent_runtime/test_platform_settings_source.py -k source_unavailable` | ["uv","run","pytest","-q","tests/agent_runtime/test_platform_settings_source.py","-k","source_unavailable"] | verified |
| E-07 | integration | 真实 PostgreSQL + 真实 Agent 定义行 | Agent 覆盖值优先；`policy_json` 反映覆盖值；读接口返回覆盖数量 | `tests/agent_runtime/test_platform_settings_source.py -k agent_override_precedence` | ["uv","run","pytest","-q","tests/agent_runtime/test_platform_settings_source.py","-k","agent_override_precedence"] | verified |
| E-08 | integration | 真实 PostgreSQL + 独立进程（无 TTL、无重启） | 保存后**新** Run 的 `policy_json` 用新 revision；跨进程无缓存等待窗口 | `tests/agent_runtime/test_platform_settings_source.py -k new_revision_without_restart` | ["uv","run","pytest","-q","tests/agent_runtime/test_platform_settings_source.py","-k","new_revision_without_restart"] | verified |

### Acceptance Evidence

**RED（先写测试，未实现）**：`uv run pytest -q tests/agent_runtime/test_platform_settings_source.py -k "source_unavailable or agent_override_precedence or new_revision_without_restart"`
```
ImportError: cannot import name 'get_platform_settings_client' from 'muad_agent_runtime.api.deps'
ERROR tests/agent_runtime/test_platform_settings_source.py
1 error in 0.09s
```

**GREEN（逐条命令与数字）**
- `uv run pytest -q tests/agent_runtime/test_platform_settings_source.py -k source_unavailable` → `1 passed, 4 deselected in 1.11s`
- `uv run pytest -q tests/agent_runtime/test_platform_settings_source.py -k agent_override_precedence` → `1 passed, 4 deselected in 0.82s`
- `uv run pytest -q tests/agent_runtime/test_platform_settings_source.py -k new_revision_without_restart` → `1 passed, 4 deselected in 0.75s`
- 整个文件：`uv run pytest -q tests/agent_runtime/test_platform_settings_source.py` → `5 passed in 2.16s`
- `uv run pytest -q tests/agent_runtime` → `350 passed in 11.19s`
- `uv run pytest -q tests/agent_core` → `109 passed in 7.79s`
- verifier `harness-snapshot#RULE-snapshot-001`：`uv run pytest -q tests/agent_runtime/test_snapshot_freeze.py tests/agent_runtime/test_run_reaper.py` → `4 passed in 0.28s`；`uv run pytest -q tests/agent_runtime -k "executor or resolve"` → `24 passed, 326 deselected`
- verifier `harness-arch#RULE-arch-001`：`uv run pytest -q tests/architecture` → `33 passed in 0.69s`
- 机检：`uv run pytest -q tests/test_configuration_inventory.py` → `3 passed`

**落地位置（client）**：任务书写的是 `application/platform_settings_client.py`；按仓库分层（HTTP client 一律在 `infrastructure/`，端口协议在 `application/ports.py`）改落在 `apps/agent-runtime/src/muad_agent_runtime/infrastructure/platform_settings_client.py`，端口 `PlatformSettingsClient`/`PlatformSettingsSnapshot`/`NullPlatformSettingsClient` 在 `application/ports.py`。装配点 `main.py` lifespan 与 `api/deps.py::get_platform_settings_client`。

**关键断言的断言位置**
- 取快照失败即失败 + 调用方计数：`ConsolePlatformSettingsClient.fetch_snapshot` 的 `_record_failed()`（`infrastructure/platform_settings_client.py:82`）与 `_create_run` 首行的 `fetch_snapshot` 调用（`application/run_service.py:654`）。E-03 用例在真实 Runtime uvicorn（真实 socket）上 POST `/v1/runs`，断言 500 + `COMMON_INTERNAL_ERROR`，再从 `/metrics` 读 `platform_settings_fetch_total{caller="runtime",result="failed"}` 增量 `before+1`，并断言 `RuntimeSnapshot` 计数为 0（无默认值被冻结）。
- 冻结值与实际用值不分叉、且只解析一次：`_create_run` 中 `snapshot_settings = fetch_snapshot(...)` → `resolve_compaction_settings(..., platform_overrides=snapshot_settings.settings.get("compaction"))` → `self._build_snapshot(..., compaction)`（`application/run_service.py:654-680`）；`resolve_compaction_settings` 改为纯函数、无缓存（`application/context_settings.py`）。
- Agent 覆盖优先：`merge_compaction_payload(base=platform_overrides, override=budget.get("compaction"))`（`application/context_settings.py:31`）；E-07 用例断言 `policy_json["compaction"]["snip"]["max_groups"]==60`（Agent 值）而平台为 40，未覆盖的 `micro.enabled` 用平台 `True`。
- 只发生一次、不进热路径：`test_run_creation_fetches_snapshot_exactly_once` 用计数 client 断言 `len(calls)==1`，执行器产出全部事件（含模型/工具回合）后计数不变。
- 删 TTL：`ContextSettingsCache`/`_default_cache`/`default_settings_cache`/`context_settings_cache_ttl_sec` 全部删除（`grep` 零命中）；`resolve_compaction_settings` 不再有隐式默认源。

**E-08 证伪旧设计（必做）**：临时在 client 内加回一个 10 秒进程级 TTL 缓存后复跑 `-k new_revision_without_restart`，用例立即变红 `assert 50 == 41`（读到缓存旧默认值而非新 revision 41）；移除该缓存后复绿。证明该用例确实能证伪「TTL 单例」旧设计（探针已还原，未进交付）。

**真实边界**：真实 PostgreSQL（`control.platform_setting` 版本行、`control.agent_definition` 覆盖行、`runtime.runtime_snapshot.policy_json` 冻结行）；真实 Console 内部端点 `GET /internal/v1/platform-settings`（真实 uvicorn 单进程监听 127.0.0.1 真实 socket，Runtime 的 client 经真实 TCP 取快照，带 `X-Internal-Service` + `X-Tenant-Id` + `X-Caller-Service: runtime`）；E-03 的 Console 端点连到已释放端口（真实连接被拒）；E-08 每个新 Run 新建 service+client（无进程内缓存、无重启、10 秒窗口内立即生效）。E-07 的 admin 读接口经真实登录会话读取 `overridden_by_resources`。

**CSV 同步说明**：运行机检发现 `docs/configuration-inventory.csv` 在本任务开工前已对多个文件过期（TASK-003/004 新增的 console 常量、`platform_settings` 契约字段、`error_codes` 等未回填，机检本就红）。为满足本任务机检项，按机检的 AST 口径整体重生成 CSV：派生的 `python-constant`/`settings` 行按源码行号重算，非派生行（frontend-constant/function-default 等）原样保留，并修正 `api/deps.py` 的 `POD_NAME` 行号（因新增 import 下移一行）。
- E-03: verified — automated command passed; run_id=947676192dc945399f80be9e1ccfa260 (confirmed_by: runner)
- E-07: verified — automated command passed; run_id=947676192dc945399f80be9e1ccfa260 (confirmed_by: runner)
- E-08: verified — automated command passed; run_id=947676192dc945399f80be9e1ccfa260 (confirmed_by: runner)

### Log
- [2026-10-05] created (draft)
- [2026-10-05] started
- [2026-10-05] completed (done)

---

## TASK-006: Worker 读取缝与任务默认接入

- **Status**: done
- **Priority**: P0
- **Depends**: TASK-004
- **Source**: platform-settings.backend.design.md#3.2 架构设计, platform-settings.backend.design.md#2.3 功能方案
- **Spec-Refs**: harness-worker#RULE-worker-001
- **Acceptance-Refs**: S-02, E-19

### Description

Worker 在**任务开始执行**与**投递记录开始尝试**两个边界各取一次设置快照：任务默认（`task.default_deadline_hours`/`max_attempts`/`batch_max_concurrency`/`misfire_grace_sec`）与投递重试策略（`task.delivery_max_attempts`/`delivery_backoff_base_sec`）。不改变 claim/lease 语义（PG 仍是唯一权威源）。

### Checklist

- [x] Worker 侧取快照客户端（复用 `scheduler/client.py` 的服务身份口径），只在任务开始执行与投递尝试两个边界调用，**不进入轮询循环**
- [x] 任务默认接入：新建 Task 用平台默认；`batch_max_concurrency` 不得超过环境项 `batch_platform_limit`
- [x] 投递重试策略接入：尝试次数上限与退避基数读平台设置，**不重置已发生的尝试次数**（不新增冻结列）
- [x] 既有 Task 行的 deadline/attempt 字段**不被改写**
- [x] Worker 侧 `locale.default_locale`（投递文案 `build_delivery_message(task, locale, …)`）改读平台设置快照
- [x] **一次性切换、不留双源**：`SharedSettings` 的 `task_default_deadline_hours`/`task_max_attempts`/`batch_max_concurrency`/`misfire_grace_sec`/`delivery_max_attempts`/`delivery_backoff_base_sec` 六项随之删除，`.env.example` 同步移除（`batch_platform_limit` 保留作容量上界）
- [x] [E-19][integration] 覆盖新 Task 用新默认 + 存量行不变 + 设置源不可读时任务创建明确失败；真实边界：**真实 PostgreSQL + 真实 Worker 应用层**（真实 claim 语义）
- [x] [S-02][E2E] 编写 E2E 验收测试并登记可单独执行的命令（真实边界：Console API → PG → 真实 Worker 进程）；不在编码期执行 RED/GREEN，统一留给 verify-e2e
- [x] [S-02] 断言新 Task 使用新默认、既有 Task 行不变
- [x] 先写测试并记录 RED，再实现
- [x] 同步 `docs/configuration-inventory.csv` 与机检期望（`task_default_deadline_hours` 等由环境项改业务项）
- [x] verifier `harness-worker#RULE-worker-001`：`uv run pytest -q tests/agent_worker && uv run pytest -q tests/agent_runtime --ignore=tests/agent_runtime/test_runner_executor.py`（真实边界：真实 PG 权威源与真实 lease/claim）
- [x] 运行验收命令并填写 Acceptance Evidence

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|--------|---------|--------------------|---------|----------------|---------|------|
| S-02 | E2E | 真实 Console API → 真实 PostgreSQL → 真实 Worker 进程（真实 lease/claim） | 新 Task 使用新默认；既有 Task 行字段不变 | planned | - | e2e_deferred |
| E-19 | integration | 真实 PostgreSQL + 真实 Worker 应用层（真实 lease/claim 语义） | 新 Task 用新默认；存量行不改写；设置源不可读 ⇒ 创建明确失败 | `tests/agent_worker/test_task_defaults_from_settings.py -k new_task_defaults` | ["uv","run","pytest","-q","tests/agent_worker/test_task_defaults_from_settings.py","-k","new_task_defaults"] | verified |

### Acceptance Evidence

> functional 在编码期填写 RED/GREEN；S-02 为 E2E，只登记，留给 verify-e2e。

**RED（先写测试、未实现前逐字原文）**
```
$ uv run pytest -q tests/agent_worker/test_task_defaults_from_settings.py -k new_task_defaults
==================================== ERRORS ====================================
___ ERROR collecting tests/agent_worker/test_task_defaults_from_settings.py ____
ImportError while importing test module 'tests/agent_worker/test_task_defaults_from_settings.py'.
Hint: make sure your test modules/packages have valid Python names.
Traceback:
tests/agent_worker/test_task_defaults_from_settings.py:27: in <module>
    from muad_agent_worker.application.ports import PlatformSettingsSnapshot
E   ModuleNotFoundError: No module named 'muad_agent_worker.application.ports'
ERROR tests/agent_worker/test_task_defaults_from_settings.py
!!!!!!!!!!!!!!!!!!! Interrupted: 1 error during collection !!!!!!!!!!!!!!!!!!!!
1 error in 0.08s
```

**GREEN（E-19 契约命令，逐字）**
```
$ uv run pytest -q tests/agent_worker/test_task_defaults_from_settings.py -k new_task_defaults
........                                                                 [100%]
8 passed in 0.97s
```
8 个用例覆盖：新 Task 用平台默认（`task.max_attempts=7`、`default_deadline_hours=2`）、存量行 `max_attempts`/`deadline_at` 不变、真实 claim/执行存量行不改写、设置源不可读 ⇒ 创建失败 + 调用方失败计数、client 服务身份（`X-Internal-Service`/`X-Tenant-Id`/`X-Caller-Service: worker`）、投递文案 locale（`en-US`）、投递退避基数与尝试上限（含"不重置已发生次数"）、调度 misfire 宽限与新 Task 默认、批量并发与 `batch_platform_limit` 取小。

**Rule verifier `harness-worker#RULE-worker-001`（真实结果）**
```
$ uv run pytest -q tests/agent_worker && uv run pytest -q tests/agent_runtime --ignore=tests/agent_runtime/test_runner_executor.py
251 passed in 12.24s
342 passed in 11.69s
```

**机检与环境项收敛**
```
$ uv run pytest -q tests/test_configuration_inventory.py
3 passed in 0.79s
$ uv run pytest -q tests/gateway tests/test_configuration_inventory.py tests/test_platform_settings_schema.py
426 passed in 68.56s
$ uv run pytest -q tests/agent_runtime -k platform_settings
5 passed, 345 deselected in 2.38s   # TASK-005 的缝未被打坏
```
`docs/configuration-inventory.csv`：删去六条 `shared-setting` 行，`settings.py` 其余行的行号按新源码重排；全仓 `python-constant` 行按源码重算（`ast` 驱动，非手工）。`.env.example` 经 grep 确认**本就不含**这六个键（`batch_platform_limit` 亦不在其中，作为环境项保留），故无键可删。

**关键断言的断言位置（不得 Mock 的真实边界）**
- 新 Task 用新默认 / 存量行不变：`tests/agent_worker/test_task_defaults_from_settings.py::test_new_task_defaults_from_platform_settings`（真实 PG `task.task_execution`，新建行 vs 既有行分别回读）。
- 真实 lease/claim 不改写存量行：`::test_new_task_defaults_execution_does_not_rewrite_existing_rows`（`WorkerLoop.claim_one` 的 `FOR UPDATE SKIP LOCKED`；`_cas` 仍以 `lease_owner`/`lease_until` 为条件）。
- 设置源不可读 ⇒ 明确失败 + 调用方计数：`::test_new_task_defaults_fail_when_settings_source_unavailable`（真实 `ConsolePlatformSettingsClient` 打向刚释放的 127.0.0.1 端口；`/metrics` 的 `platform_settings_fetch_total{caller="worker",result="failed"}` +1）。
- Worker 服务身份：`::test_new_task_defaults_client_uses_worker_service_identity`（真实内部端点路径 + 三个请求头）。
- 投递 locale / 退避 / 上限：`::test_new_task_defaults_apply_to_delivery_locale`、`::test_new_task_defaults_apply_to_delivery_backoff_and_attempt_cap`（真实 `DeliveryLoop.run_once` + 真实 `delivery_attempts` 列自增）。
- 调度默认与 misfire 宽限：`::test_new_task_defaults_scheduler_uses_platform_values`（真实 `task.task_schedule` `next_fire_at` 写入 + `ScheduleService`）。
- 并发取小：`::test_new_task_defaults_batch_concurrency_capped_by_platform_limit`（`min(plan, 平台默认, batch_platform_limit)`）。
- 取快照只在边界：各用例断言注入 client 的 `calls` 恰为 `[tenant_id]`（一次）；投递队列跨租户时按有界租户前缀取，空闲轮询零调用。

**实现落点**
- client：`apps/agent-worker/src/muad_agent_worker/infrastructure/platform_settings_client.py`（`ConsolePlatformSettingsClient`，`X-Caller-Service: worker`；失败在调用方侧计数）。
- 端口/快照：`application/ports.py`；解析缝：`application/platform_settings.py`；指标：`metrics.py` 的 `platform_settings_fetch_total{caller,result}`。
- 边界接线：`TaskService.create`、`SchedulerLoop._process`→`fire`→`_insert_task`、`DeliveryLoop.run_once`→`_attempt_delivery`、`BatchFanoutService.fan_out`。
- 生产装配：`main.py` lifespan 注入真实 client（退出时 `aclose()` 并回落空对象，避免同进程残留一个已切断的源）。

**S-02（E2E，只登记，不执行）**
- 真实边界：真实 Console API（`PUT/GET /api/v1/platform-settings` 由管理员保存 `task.max_attempts` 新值）→ 真实 PostgreSQL（`control.platform_setting` 版本行 + `task.task_execution`）→ 真实 Worker 进程（真实 `FOR UPDATE SKIP LOCKED` claim/lease）→ 真实 Task 行。
- 断言：保存后**新建**的 Task 用新默认（`max_attempts`/`deadline_at` 与新版本一致）；**既有 Task 行**的 `max_attempts`/`deadline_at` 逐字不变。
- 命令留 `-`，交由需求级 `verify-e2e`；编码期不执行其 RED/GREEN，也不降级成 integration。

**与设计的偏差 / 需下一任务接手（本任务未覆盖）**
- 验收栈仍以环境键注入被删除的两个 task 项：`tests/acceptance/dfx/environment.py` 的 `DELIVERY_BACKOFF_BASE_SEC`（与 `test_dfx_delivery.py` 的窗口常量 2 同源）、`tests/acceptance/task_schedule/environment.py:423`、`tests/acceptance/im_gateway/environment.py:205`，以及 `tests/acceptance/dfx/test_dfx_fault_matrix.py:721` 的**按 Worker 进程**覆盖 `TASK_MAX_ATTEMPTS=1`。这些键删除后注入**已失效**：改动后的正确形态是在各栈按租户种一行 `control.platform_setting`（`replacement`），而这属于验收栈的改造，本任务未承接（属"与设计的偏差"，需下一个任务或需求级收口处理）。已在两处把会直接 `AttributeError` 的 `SharedSettings().delivery_max_attempts` 改为 schema 默认（值同为 5，行为不变）。
- 投递队列**跨租户**而设置**按租户**：实现为"先按设置无关谓词取有界租户前缀，再逐个取快照判定到期"（`MAX_TENANTS_PER_TICK=8`），避免某租户停在退避窗口时阻塞整条队列；代价是这些租户在退避等待期内每个轮询拍仍会各取一次快照（有界，空闲时零调用）。设计未规定该情形，此处为落地取舍。
- S-02: e2e_deferred — automated command e2e_deferred; run_id=ab4a307ef81144fb84cfa6d170c459cb (confirmed_by: runner)
- E-19: verified — automated command passed; run_id=ab4a307ef81144fb84cfa6d170c459cb (confirmed_by: runner)

### Log
- [2026-10-05] created (draft)
- [2026-10-05] started
- [2026-10-05] implemented: worker 读取缝（client/端口/边界接线）、task 六项接入、SharedSettings 六项与 CSV 收敛；E-19 8 passed；Rule verifier 251+342 passed；S-02 只登记
- [2026-10-05] completed (done)

---

## TASK-007: Gateway 读取缝与 IM 展示节拍

- **Status**: done
- **Priority**: P0
- **Depends**: TASK-004
- **Source**: platform-settings.backend.design.md#3.1 方案选型, platform-settings.backend.design.md#3.2 架构设计
- **Spec-Refs**: harness-im#RULE-im-001, harness-im#RULE-im-002
- **Acceptance-Refs**: B-03

### Description

IM 进度节拍（`im.progress_interval_sec`）改由平台设置提供，Gateway 在**每条入站消息的回复生命周期开始时**取一次并固定（ADR-04）。渠道适配器一行不改，核心域不新增渠道专有字样。

### Checklist

- [x] Gateway 取快照（复用 `application/console_client.py` 的服务身份口径），调用点只有「回复生命周期开始」一处
- [x] 回复期间节拍固定：不得在每次 tick 重新读取；下一条消息用新值
- [x] `PROGRESS_INTERVAL_SEC` / `im_progress_interval_sec` 的散落默认收敛为平台设置 + schema 默认；`im_progress_updates_per_second` **保留在环境**（服务资源预算上限）
- [x] Gateway 侧 `locale.default_locale`（回复渲染）改读平台设置快照
- [x] 一次性切换：`SharedSettings.im_progress_interval_sec` 随之删除、`.env.example` 同步移除（`im_progress_updates_per_second` 保留在环境）；`.env.example` 的 `IM_PROGRESS_INTERVAL_SEC`（:19）一并摘掉
- [x] **改写被本改动打断的既有用例**：`tests/gateway/test_execution_progress.py:378-382` 现在直接断言 `SharedSettings().im_progress_interval_sec` 的 5.0/1.0，字段删除后会红——按新缝改写（`im_progress_updates_per_second` 保留在环境）
- [x] [B-03][unit] 覆盖节拍取值边界（`ge=1.0` 下界）与「一条回复内多次 tick 节拍不变、下一条消息用新值」；真实边界：**真实 Gateway 取值函数，不 mock**
- [x] 渠道中立：`channels/` 与适配器零改动；跑机检 `tests/architecture/test_channel_neutrality.py` 三条断言
- [x] 先写测试并记录 RED，再实现
- [x] 同步 `docs/configuration-inventory.csv` 与机检期望
- [x] verifier `harness-im#RULE-im-001`：`uv run pytest -q tests/console_channel tests/gateway`（真实边界：真实渠道绑定表 + 真实 Gateway）
- [x] verifier `harness-im#RULE-im-002`：`uv run pytest -q tests/architecture/test_channel_neutrality.py`（真实边界：真实源码树三条中立性断言）
- [x] 运行验收命令并填写 Acceptance Evidence

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|--------|---------|--------------------|---------|----------------|---------|------|
| B-03 | unit | 真实 Gateway 回复生命周期取值函数（无服务） | 下界 `1.0` 接受、更小被拒；回复期间节拍不变；下一条消息生效 | `tests/gateway/test_progress_settings.py` | ["uv","run","pytest","-q","tests/gateway/test_progress_settings.py"] | verified |

### Acceptance Evidence

**B-03（unit · 真实 Gateway 回复生命周期取值函数，无服务）**

RED（先写测试、后实现，`tests/gateway/test_progress_settings.py` 已落盘）：

```
$ uv run pytest -q tests/gateway/test_progress_settings.py
ERROR collecting tests/gateway/test_progress_settings.py
E   ModuleNotFoundError: No module named 'muad_im_gateway.application.ports'
!!!!!!!!!!!!!!!!!!!! Interrupted: 1 error during collection !!!!!!!!!!!!!!!!!!!!
1 error in 0.06s
```

GREEN：

- `uv run pytest -q tests/gateway/test_progress_settings.py` → **5 passed**
- `uv run pytest -q tests/gateway` → **323 passed**（含按新缝改写的 `test_execution_progress.py`）
- verifier `harness-im#RULE-im-001`：`uv run pytest -q tests/console_channel tests/gateway` → **376 passed**
- verifier `harness-im#RULE-im-002`：`uv run pytest -q tests/architecture/test_channel_neutrality.py` → **3 passed**
- `uv run pytest -q tests/test_configuration_inventory.py` → **3 passed**
- `uv run pytest -q tests/test_settings.py tests/test_platform_settings_schema.py tests/test_configuration_inventory.py tests/gateway/test_gateway_metrics.py` → **113 passed**
- `uv run ruff check apps/ packages/ tests/gateway` → **All checks passed**

关键断言与位置（`tests/gateway/test_progress_settings.py`）：

- **下界**：`test_b03_interval_floor_accepts_one_and_rejects_below`——`resolve_reply_settings` 走真实 `parse_platform_settings`（未 mock），`im.progress_interval_sec = 1.0` 接受；`0.5` / `0.0` 抛 `AppError`。
- **默认单一来源**：`test_b03_defaults_when_tenant_has_no_record`——`revision 0` ⇒ `("zh-CN", 5.0)`。
- **一回复一取 + 节拍固定 + 下一条用新值**：`test_b03_one_fetch_per_reply_and_next_message_uses_new_value`——两次 `handle` 断言 `settings_client.calls == ["tenant-1", "tenant-1"]`（tick 不重取快照）且 `recorded_intervals == [1.0, 5.0]`（每条回复只调一次 `iter_with_ticks`、值取自该条回复自身的快照，下一条入站消息改用新值）。
- **locale 逐条切换**：`test_b03_reply_render_locale_comes_from_snapshot`——同一 pipeline 两条消息分别以 `en-US` / `zh-CN` 渲染错误文案，断言 `adapter.sent[-2]/-1` 等于各自 locale 的词条且互不相等（Gateway 不再读启动 settings 的 locale）。
- **client 契约与失败计数（调用方侧）**：`test_b03_client_uses_internal_contract_and_records_failed_metric`——`httpx.MockTransport` 下断言真实路径 `GET /internal/v1/platform-settings`、头 `X-Internal-Service` / `X-Tenant-Id` / `X-Caller-Service: gateway`；失败后 `platform_settings_fetch_total{caller="gateway",result="failed"}` 恰 +1。

真实边界说明：取值函数（`resolve_reply_settings` → `parse_platform_settings`）与设置源**未 mock**；测试仅对 `iter_with_ticks` 做**记录式包裹**（真实函数照常运行、逐帧透传），用于观测实际下发的节拍值，不改变行为。

实现落点：

- 端口协议：`apps/im-gateway/src/muad_im_gateway/application/ports.py`（`PlatformSettingsSnapshot` / `PlatformSettingsClient` / `NullPlatformSettingsClient`）
- 解析缝：`apps/im-gateway/src/muad_im_gateway/application/platform_settings.py`（`resolve_reply_settings`，唯一解析缝）
- 取快照 client：`apps/im-gateway/src/muad_im_gateway/infrastructure/platform_settings_client.py`（复用 `application/envelope.py` 的错误码解析与服务身份口径；`X-Caller-Service: gateway`）
- **调用点唯一**：`application/inbound.py` 的 `handle`——`token = _REPLY_SETTINGS.set(await self._resolve_reply_settings())`（回复生命周期开始处一次），回复期间经 ContextVar 读取（并发回复各持一份，互不串味）；`_consume_run` 以 `iter_with_ticks(stream, interval=self._progress_interval_sec)` 取值。
- 一次性切换：`SharedSettings.im_progress_interval_sec` 删除；`.env.example` 摘掉 `IM_PROGRESS_INTERVAL_SEC`；`application/progress.py` 的 `PROGRESS_INTERVAL_SEC` 常量删除（下界/默认只剩 schema 一处）；`im_progress_updates_per_second` 保留在环境。
- 渠道中立：`channels/` 与适配器零改动（`git status` 无 `channels/` 变更）；读设置全在 `application/` 层。
- 盘点：`docs/configuration-inventory.csv` 重算（新增 gateway client / metrics 常量行，删除 `PROGRESS_INTERVAL_SEC` 与 `inbound.__init__.progress_interval_sec` 行，行号重排）。

设计偏差/交接（供 TASK-013/014 接手）：

- 为兑现「locale 逐条回复固定」，`_activity_messages` 由 pipeline 实例级缓存改为**按 locale 缓存、随 `_RunStreamState` 绑定本条回复**（`_send_status` 从 `state.activity_messages` 取值）——否则并发两条不同 locale 的回复会互相串文案。
- `test_execution_progress.py` 的 `_pipeline_with_progress(interval=0.01)` 改为 `interval=1.0`：节拍下界 `ge=1.0` 由 schema 把住，测试无法再注入亚秒值；该用例的「主路径状态先于 tick」语义不变（第 4 次写入仍是首个 tick，`armed` 在 ~1s 触发）。
- `tests/acceptance/im_gateway/environment.py:142` 仍注入 `IM_PROGRESS_INTERVAL_SEC=1`（已失效）——该栈改「按租户种平台设置」归 **TASK-014**。
- B-03: verified — automated command passed; run_id=ff1ef3adb97f4c9fbaeed76fbb9cb9fe (confirmed_by: runner)

### Log
- [2026-10-05] created (draft)
- [2026-10-05] started
- [2026-10-05] completed (done)

---

## TASK-008: 模型执行预算层级

- **Status**: done
- **Priority**: P0
- **Depends**: TASK-001
- **Source**: platform-settings.backend.design.md#3.1 方案选型
- **Spec-Refs**:
- **Acceptance-Refs**: B-02

### Description

按 ADR-07 把四套互不相关的超时/重试常量改成**层级**：总预算 `agent.deadline_ms` → 单次请求预算 = min(上限, 剩余总预算) → 重试预算 ≤ 剩余总预算；`MAX_MODEL_RETRIES`/`MAX_RETRIES_DEFAULT`/`AgentPolicy.max_model_retries` 三处合一，退避基数两处合一（算法参数，不开放为设置）。

### Checklist

- [x] 定义预算层级解析函数（总 deadline → 单次请求预算 → 重试预算），落在 agent-core/agent-runtime 的模型调用装配侧
- [x] 三处重试常量合一为 `agent.max_model_retries`；`RETRY_BASE_SEC` 与 `DEFAULT_RETRY_BASE_SEC` 合一（退避基数不是设置项）
- [x] `ModelGateway.DEADLINE_DEFAULT_MS` / `executor.MODEL_TIMEOUT_SEC` / Provider I/O timeout 不再各自独立取默认，改由层级派生
- [x] [B-02][unit] 覆盖：单次预算 > 剩余总预算时被夹到剩余；重试预算之和不得超过总预算；总预算耗尽前停止重试；真实边界：**真实预算解析函数，不 mock**
- [x] 先写测试并记录 RED，再实现
- [x] 同步 `docs/configuration-inventory.csv` 与机检期望
- [x] 运行验收命令并填写 Acceptance Evidence

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|--------|---------|--------------------|---------|----------------|---------|------|
| B-02 | unit | 真实预算层级解析函数（无服务） | 单次预算 ≤ 剩余总预算；重试预算不越总预算；耗尽即停止 | `tests/agent_runtime/test_model_budget_layers.py` | ["uv","run","pytest","-q","tests/agent_runtime/test_model_budget_layers.py"] | verified |

### Acceptance Evidence

**层级实现落点与理由**：`packages/agent-core/src/muad_agent_core/model/budget.py`（`ModelBudget` + `resolve_*` 派生方法）。

- 落 **agent-core 的 model 侧**：重试算法（`AgentRunner._complete_with_recovery`）、总预算/重试次数载体（`AgentPolicy`）、模型 Provider I/O 超时（`OpenAICompatibleProvider`）都在 agent-core；agent-runtime 依赖 agent-core，反向不成立。
- 放 `model/` 而非 `agent/`：Provider（`model/openai_provider.py`）要从同一处取单次请求上限；若放 `agent/`，`model → agent.budget` 会经 `agent/__init__` 反向触发 `runner → model` 形成导入环。放 `model/budget.py` 两个消费方都只依赖 contracts，无环（已由全量 `tests/agent_core` / `tests/agent_runtime` 导入验证）。

**RED（先写测试，未实现时真实输出）**：

```
$ uv run pytest -q tests/agent_runtime/test_model_budget_layers.py
ERROR collecting tests/agent_runtime/test_model_budget_layers.py
ImportError while importing test module
tests/agent_runtime/test_model_budget_layers.py:12: in <module>
    from muad_agent_core.agent.budget import (
E   ModuleNotFoundError: No module named 'muad_agent_core.agent.budget'
!!! Interrupted: 1 error during collection !!!
1 error in 0.06s
```

（实现期把模块最终定为 `muad_agent_core.model.budget`，测试导入同步改为 `muad_agent_core.model`，理由见上。）

**GREEN（命令与数字）**：

- `uv run pytest -q tests/agent_runtime/test_model_budget_layers.py` → `7 passed in 0.01s`
- `uv run pytest -q tests/agent_runtime` → `357 passed in 11.87s`（含 `test_snapshot_freeze.py` / `test_runs_api.py` / `test_context_compaction_config.py` / `test_platform_settings_source.py` 等被 `snapshot_policy` 改动辐射的用例）
- `uv run pytest -q tests/agent_core` → `109 passed in 7.81s`（含 `test_runner.py` 既有重试/截止用例）
- `uv run pytest -q tests/test_configuration_inventory.py` → `3 passed in 0.69s`
- `uv run ruff check`（7 个改动文件）→ `All checks passed!`

**关键断言位置（`tests/agent_runtime/test_model_budget_layers.py`，真实函数，无 mock）**：

- 单次预算被夹到剩余：`test_request_budget_is_clamped_to_remaining_total_budget`（`request_timeout_ms(elapsed_ms=80_000)==10_000`、`elapsed_ms>=deadline` 时为 `0`）
- 单次预算被算法上限夹住：`test_request_budget_is_capped_by_the_algorithm_ceiling`（`== MAX_MODEL_REQUEST_MS`）
- 重试预算不越总预算：`test_retry_budget_never_exceeds_total_budget`（`retry_budget_ms() <= deadline_ms`，紧预算时被夹到 `deadline_ms`）
- 耗尽即停止：`test_retries_stop_before_total_budget_is_exhausted`（累计退避 `sum(delays)*1000 <= deadline_ms` 且用不满次数）、`test_retry_stops_when_attempts_are_exhausted`
- 退避基数单处：`test_retry_after_header_overrides_the_backoff_base`（`retry_delay_sec(0)==RETRY_BASE_SEC`、`(1)==RETRY_BASE_SEC*2`）
- 三处常量同源：`test_defaults_and_policy_fields_track_the_platform_schema`（`DEFAULT_*` == `AgentSettings()` 默认；`AgentPolicy()` 同步）

**真实边界（无服务）与落点证据**：

- 层级派生由 `ModelBudget` 方法直接承担（`request_timeout_ms` / `retry_budget_ms` / `retry_delay_sec` / `fits_before_deadline` / `allows_retry`），两个重试循环与 Provider 超时均从它取数：`packages/agent-core/src/muad_agent_core/agent/runner.py`（`AgentPolicy` 默认值与 `_retry_delay`）、`apps/agent-runtime/.../application/model_gateway.py`（`self._budget`）、`apps/agent-runtime/.../application/executor.py`（`agent_policy_for` + `budget.request_timeout_sec()`）。
- `snapshot_policy(compaction, agent_budget)` 冻结 `deadline_ms`/`max_model_retries`；`_create_run` 沿用 TASK-005 取好的快照（`agent_budget_from_platform(snapshot_settings.settings)`，无新取数方式、无 TTL）；`_resume_run` 经 `agent_budget_of(policy_json)` 读回冻结值。
- 纯逻辑装配烟测（真实函数、无服务）：平台文档 `{"agent":{"deadline_ms":45000,"max_model_retries":7}}` → `agent_budget_from_platform` → `snapshot_policy` 冻结 `{deadline_ms:45000,max_model_retries:7}` → `agent_budget_of` 还原 → `agent_policy_for` 得 `AgentPolicy(deadline_ms=45000,max_model_retries=7)`；Agent `runtime_config.deadline_ms=9000` 覆盖为基值之上更高优先级（`deadline_ms=9000,max_model_retries=7`）。
- B-02: verified — automated command passed; run_id=718d9ac0af6b475e9aea4b970cce1a4c (confirmed_by: runner)

### Log
- [2026-10-05] created (draft)
- [2026-10-05] started
- [2026-10-05] completed (done)

---

## TASK-009: Console 侧业务默认接入（认证策略与 MCP 规模）

- **Status**: done
- **Priority**: P0
- **Depends**: TASK-003, TASK-004
- **Source**: platform-settings.backend.design.md#2.3 功能方案, platform-settings.backend.design.md#3.4 接口设计
- **Spec-Refs**: harness-mcp#RULE-mcp-001
- **Acceptance-Refs**: E-15

### Description

把 Console 侧的业务默认接入平台设置：认证策略（`auth.min_password_length`/`max_failed_attempts`/`lock_duration_minutes`/`session_ttl_hours`，其中会话时长与 Cookie `Max-Age` 同源、滑动阈值派生）与 MCP 接入规模（`mcp.max_tools_per_server`）。MCP 连接参数继续留在 MCP 页面。

### Checklist

- [x] 认证策略读平台设置：`SESSION_TTL` 与 Cookie `Max-Age` **同源**，`SLIDE_THRESHOLD` 保持派生；存量会话到期时间不变
- [x] `MIN_PASSWORD_LENGTH`/`MAX_FAILED_ATTEMPTS`/`LOCK_DURATION` 由平台设置提供，服务端校验为准
- [x] `mcp.max_tools_per_server` 接入发现/接入路径；MCP 连接参数（`connect_timeout_ms`/`tool_cache_ttl_sec`）**不改**，仍由 MCP 页面管理；同批删除 `SharedSettings.mcp_max_tools_per_server`（消费者只有 `application/mcp_service.py:430`）并从 `.env.example` 摘掉同名键（若在）
- [x] **api-kit 的 locale 兜底（TASK-013 已改动，需复核）**：`install_api_foundation(app, default_locale=None)` 现在是 `default_locale or "zh-CN"`（`packages/api-kit/src/muad_api/app.py:18-28`），**不再**读 `SharedSettings.default_locale`。复核它与平台 `locale.default_locale` 的关系：请求级 `X-Locale`/`Accept-Language` 优先，平台默认语言应在**无语言信号**时生效——若判断 api-kit 只该做框架兜底、平台语言由别处承担，必须在证据里写明理由与承担位置，不要沉默放过
- [x] API-05 已认证限额端点接入（供前端复用 Skill 导入限额）
- [x] [E-15][integration] 覆盖新签发会话按新 TTL、已签发会话到期不变、新密码按新长度校验；真实边界：**真实 PostgreSQL + 真实登录会话与 CSRF**
- [x] 先写测试并记录 RED，再实现
- [x] 同步 `docs/configuration-inventory.csv` 与机检期望
- [x] verifier `harness-mcp#RULE-mcp-001`：`uv run pytest -q tests/console_mcp/test_mcp_rules.py`（真实边界：真实 MCP 规则套件；连接参数仍留 MCP 页面）
- [x] 运行验收命令并填写 Acceptance Evidence

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|--------|---------|--------------------|---------|----------------|---------|------|
| E-15 | integration | 真实 PostgreSQL + 真实登录会话与 CSRF | 新会话按新 TTL（Cookie 与 Session 同源）；旧会话到期不变；密码长度新值生效、存量不回改 | `tests/console_platform/test_auth_policy_settings.py` | ["uv","run","pytest","-q","tests/console_platform/test_auth_policy_settings.py"] | verified |

### Acceptance Evidence

#### RED（先写测试，实现前失败原文）

实现前跑契约命令（`uv run pytest -q tests/console_platform/test_auth_policy_settings.py --tb=line`）：

```
4 failed, 1 passed
test_auth_policy_settings.py:157: AssertionError: ['muad_session=...; HttpOnly; Max-Age=43200; Path=/; SameSite=strict', 'muad_csrf=...; Max-Age=43200; ...']   # 期望 10800（3h）
test_auth_policy_settings.py:208: assert <expires_at> > (now + timedelta(days=1, seconds=82800))       # 未按当前 48h TTL 续期
test_auth_policy_settings.py:241: assert 'INVALID_CREDENTIALS' == 'ACCOUNT_LOCKED'                    # max_failed_attempts=2 未生效
test_auth_policy_settings.py:286: AssertionError: {...200 OK...}                                       # min_password_length=20 未生效，12 位新密码仍被接受
```

（`test_existing_session_expiry_unchanged_when_ttl_changes` 在 RED 时即通过——它断言的是"不改写已签发会话"的**不变式**，实现前后都必须成立。）

#### GREEN（命令与数字）

```
$ uv run pytest -q tests/console_platform/test_auth_policy_settings.py
5 passed in 0.74s
$ uv run pytest -q tests/console_platform tests/console_auth
206 passed in 24.81s
$ uv run pytest -q tests/console_mcp/test_mcp_rules.py          # verifier harness-mcp#RULE-mcp-001
3 passed in 0.01s
$ uv run pytest -q tests/console_mcp
36 passed in 8.47s
$ uv run pytest -q tests/test_configuration_inventory.py
3 passed in 0.65s
$ uv run pytest -q tests/console_internal tests/architecture tests/test_contracts.py tests/test_platform_settings_schema.py
180 passed in 3.19s
```

#### 断言位置（`tests/console_platform/test_auth_policy_settings.py`）

- **Cookie 与 Session 同源**：`:150-164` —— 设置 `session_ttl_hours=3` 后 `POST /api/v1/auth/login`，两枚 cookie 的 `Max-Age` 都等于 10800（`security.set_auth_cookies` 用登录返回的同一个 `timedelta`），会话行 `expires_at - issued_at == timedelta(hours=3)`。
- **已签发会话到期不变**：`:167-179` —— 登录后把设置改成 48h，直接读 `control.console_session.expires_at`，与改前逐值相等（未发生续期即不改写）。
- **续期按当前 TTL、阈值派生**：`:182-224` —— 植入"签发 TTL 48h、剩余 1h"的会话 ⇒ 剩余 < 48/2=24h 触发续期，续期后到期 = now + **当前** 48h；另植入"签发 TTL 30h、剩余 30h"的会话 ⇒ 30h ≥ 15h 不续期、到期不变（未到续期点不读设置）。阈值是派生值，非独立设置项。
- **失败锁定按设置**：`:227-245` —— `max_failed_attempts=2` / `lock_duration_minutes=30`：两次错密后第三次返回 `ACCOUNT_LOCKED`，`locked_until - now ∈ (29min, 30min]`。
- **口令长度按设置、存量不回改**：`:248-300` —— 默认策略经真实 API + CSRF 建 12 位密码账号；收紧到 20 后同一 12 位新密码被**服务端**拒绝（400 `COMMON_BAD_REQUEST`），20 位被接受；收紧前建的 12 位账号仍能登录（`200`）。

#### 实现落点

- 认证策略读取：`application/auth_service.py` 的 `AuthService._auth_policy()`（`PlatformSettingsService.read_current(tenant).settings.auth`，无新缓存/TTL）；登录/改密/建号用 `account.tenant_id`（不信任请求头）。`login()` 现返回 `(account, token, ttl)`，`api/auth.py` 把同一 `ttl` 传给 `set_auth_cookies(..., max_age_sec=...)`——Cookie 与会话行同源。
- 滑动阈值：`auth_service.resolve_session()` 用**本会话签发 TTL/2**（`expires_at - issued_at`）作阈值，仅当真要续期时才读当前设置续到 `now + 当前 ttl`；因此普通已认证请求**不**多读一次设置（守住 NFR-PERF-01），且已签发会话的到期时间不被追改。
- 常量收敛：删除 `MIN_PASSWORD_LENGTH`/`MAX_FAILED_ATTEMPTS`/`LOCK_DURATION`/`SESSION_TTL`/`SLIDE_THRESHOLD`（`auth_service.py`）与 `SESSION_MAX_AGE_SEC`（`api/security.py`）；`cli.py` 改为按目标租户当前设置校验口令长度。
- MCP：`application/mcp_service.py::discover_tools` 读 `mcp.max_tools_per_server`；`SharedSettings.mcp_max_tools_per_server` 已删除，`.env.example` 与 `deploy/k8s/base/configmap.yaml` 均无该键（复核无残留）。**连接参数 `connect_timeout_ms`/`tool_cache_ttl_sec` 未动**；超限仍走 `McpClientError("protocol", "tool count exceeds limit N")` → `MCP_DISCOVERY_FAILED` 且保留上一成功 Catalog（不截断）。
  - 接线证据：`tests/console_mcp/test_discover_api.py::test_tool_limit_comes_from_platform_settings`（探针真实返回 2 个工具、平台设置上限设为 1 ⇒ 只有读到设置才会失败；断言 `last_discovery_error` 含 `limit 1`）。
- 规范与清单同步：`docs/configuration-inventory.csv`（删 6 条认证常量 + `mcp_max_tools_per_server` 的 `shared-setting` 行，重算 38 处行号；机检 `tests/test_configuration_inventory.py` 绿）、`docs/configuration-inventory.md`、`.code-flow/specs/mcp/harness-mcp.md`（工具数上限来源由 `mcp_max_tools_per_server` 改为平台设置 `mcp.max_tools_per_server`）。

#### 复核结论（两条 Checklist 专项）

- **API-05 已认证限额端点**（`api/platform_settings.py:289-292`）：返回的 `skill_import` 三个限额直接取自 `infrastructure/skill_validator.py` 的 `ZIP_BYTES_LIMIT`/`UNPACKED_BYTES_LIMIT`/`ENTRY_LIMIT`——正是导入路径（`skill_validator.validate_skill_package`、`api/skills.py:27-28`）实际执行的同一组常量，**服务端单一来源**，无需改动；既有 `tests/console_platform/test_platform_settings_api.py:297` 覆盖。
- **api-kit locale 兜底复核**：结论是"**api-kit 只做框架兜底，平台语言不由它承担**"，故不改 api-kit。理由与承担位置：
  - `install_api_foundation` 在**进程导入期**调用（四个 `main.py`），且 API 错误文案是**每请求**由 `X-Locale`/`Accept-Language` 解析（`middleware.RequestContextMiddleware:38-39`）后回退到常量；该常量是**进程级**的，而 `locale.default_locale` 是**按租户**的 DB 值——中间件在解析语言时**拿不到**（也不应拿）租户（`/api/v1/auth/login` 等公开路由无主体），把按租户的值塞进进程常量在架构上不成立。
  - Console 前端**总是**发 `X-Locale`（`apps/console-platform/frontend/src/api/client.ts:79`，取自用户当前 UI 语言）⇒ 浏览器请求不存在"无语言信号"的缺口；常量兜底只对不带语言头的调用者（curl/内部服务）生效。
  - 平台默认语言的真正承担位置是**业务渲染边界**：Worker 投递文案（`apps/agent-worker/.../delivery/service.py:155` 读 `platform.locale.default_locale`）与 Gateway 回复渲染（`apps/im-gateway/.../application/platform_settings.py:46`），二者已由 TASK-006/007 切换（与 TASK-013 的收口说明一致）。**不沉默放过**：此处记录为"已复核、判定为不需要改动"，非遗漏。
- E-15: verified — automated command passed; run_id=52e90b3cae384b4ab978a1cf209708b9 (confirmed_by: runner)

### Log
- [2026-10-05] created (draft)
- [2026-10-05] started
- [2026-10-05] completed (done)

---

## TASK-010: Console 系统设置页

- **Status**: done
- **Priority**: P0
- **Depends**: TASK-004
- **Source**: platform-settings.frontend.design.md#2.2 功能方案, platform-settings.frontend.design.md#3.2 页面与路由结构, platform-settings.frontend.design.md#3.3 组件设计, platform-settings.frontend.design.md#3.5 状态与数据流
- **Spec-Refs**: harness-frontend#RULE-front-001, harness-ui#RULE-ui-001, harness-i18n#RULE-i18n-001
- **Acceptance-Refs**: S-01, S-03, E-11, E-12, E-13, E-14, B-05, B-06, B-08

### Description

新增系统设置页（分组表单、平台默认/资源覆盖/生效方式、版本历史与回滚），菜单由固定十项改为十一项，路由受 ADMIN 守卫；改写既有的 shell 契约测试（含删除「不得存在系统设置入口」的反向断言）。

### Checklist

- [x] `src/modules/settings/`：`pages/SettingsPage.tsx` + `services/settingsApi.ts`（import 共享 `src/api/client.ts` 实例）+ hooks + types；组件树按 design §3.3（CMP-01..CMP-09，容器/展示分离）
- [x] 字段控件按 API 返回的元数据渲染（`type/min/max/enum/default`），**组件内不得写默认值或范围字面量**
- [x] 页面不按列表页模板实现；版本历史用 SideSheet（`harness-ui-detail` 的详情形态）；回滚走二次确认
- [x] `src/config/menu.ts` 追加第 11 项 `{path:"/settings", key:"nav.settings", adminOnly:true}`；`src/App.tsx` 加路由并包 `RequireRole role="ADMIN"`；`AppLayout.tsx` 登记图标
- [x] i18n：`src/locales/{zh-CN,en-US}.json` 新增 `nav.settings`、`settings.*` 与五个生效方式标签（`applies_to` 全覆盖）；**字段/分组的词条键名按 API-01 返回的 `label_key` 逐字对齐**（组 `settings.group.<key>`、字段 `settings.field.<path>`，压缩组内是组内相对路径如 `snip.max_groups`，其余为 `group.field`），不要自创键名
- [x] **改写** `tests/frontend/test_console_shell_contract.py`：`EXPECTED_KEYS` 十一项、`adminOnly` 项由 1 变 2、删除 `test_console_shell_has_no_system_settings_entry` 并替换为入口存在的正向断言 + 路由守卫断言
- [x] 新增 `tests/frontend/test_platform_settings_contract.py`：HTTP 只经 services 层、字段元数据驱动、`applies_to` 标签齐全
- [x] 同步事实文档 `docs/00-详细设计索引与设计基线.md` 与 `docs/README.md` 中「Console 不提供系统设置菜单」的表述
- [x] [B-08][unit] 契约机检：菜单十一项 + 入口存在 + 路由守卫 + 设置页 HTTP 只经 services 层；真实边界：**真实源码树（无服务）**
- [x] [B-06][unit] `applies_to` 五个取值的标签映射与 zh-CN/en-US 词条齐备
- [x] [S-01][E2E] 编写端到端验收（真实边界：真实浏览器 → Console → PG → Runtime → 模型探针），登记可执行命令；编码期只登记，留给 verify-e2e
- [x] [S-03][E2E] 编写权限/租户端到端验收（真实登录会话与角色，无路由拦截）
- [x] [E-11][E2E] 保存失败 ⇒ 字段级错误定位
- [x] [E-12][E2E] 版本冲突 ⇒ 明确提示 + 重新加载，不静默重试
- [x] [E-13][E2E] 读取失败 ⇒ 错误态**不渲染任何值**
- [x] [E-14][E2E] BUILDER 直接访问 `/settings` 被守卫拦截
- [x] [B-05][E2E] 无改动时保存按钮禁用；版本号显示当前版本
- [x] 先写测试并记录 RED（前端契约测试 + 构建），再实现
- [x] verifier `harness-frontend#RULE-front-001`：`uv run pytest -q tests/frontend/test_api_client_contract.py && uv run python scripts/check_frontend_api_usage.py && uv run python scripts/check_frontend_i18n.py && npm --prefix apps/console-platform/frontend run typecheck`（真实边界：真实源码树 + 真实 typecheck）
- [x] verifier `harness-ui#RULE-ui-001`：`uv run pytest -q tests/frontend/test_console_shell_contract.py tests/frontend/test_ui_style_contract.py && npm --prefix apps/console-platform/frontend run build`（真实边界：真实 shell 契约 + 真实构建产物）
- [x] verifier `harness-i18n#RULE-i18n-001`：`uv run pytest -q tests/acceptance/test_foundation_i18n.py && uv run python scripts/check_frontend_i18n.py`（真实边界：真实词条文件与真实 i18n 机检）
- [x] 运行验收命令并填写 Acceptance Evidence

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|--------|---------|--------------------|---------|----------------|---------|------|
| S-01 | E2E | 真实浏览器 → 真实 Console API → 真实 PostgreSQL → 真实 Runtime → 真实模型 HTTP 探针 | 保存后新 Run 用新值（`policy_json` 与探针请求体）；保存前已开始的 Run 不变 | planned | - | e2e_deferred |
| S-03 | E2E | 真实浏览器（真实登录会话与角色）→ 真实 Console API → 真实 PostgreSQL | ADMIN 菜单有入口且在运行审计之后；BUILDER 无入口且直接访问被拒；跨租户不可见 | planned | - | e2e_deferred |
| E-11 | E2E | 真实浏览器 + 真实 Console API（后端校验真实生效） | 破坏联动组合 ⇒ 字段级错误定位；当前值不被覆盖；输入保留 | planned | - | e2e_deferred |
| E-12 | E2E | 真实浏览器 + 真实 Console API（真实 409） | 冲突提示 + 重新加载；不自动重试 | planned | - | e2e_deferred |
| E-13 | E2E | 真实浏览器 + 真实路由；读取失败用真实网络失败注入（错误路径允许） | 错误态文案 + 重试；**不渲染任何值** | planned | - | e2e_deferred |
| E-14 | E2E | 真实浏览器 + 真实登录会话（真实 403） | 被守卫拦截，不发设置请求 | planned | - | e2e_deferred |
| B-05 | E2E | 真实浏览器 + 真实路由 | 无改动时保存禁用；显示当前版本号 | planned | - | e2e_deferred |
| B-06 | unit | 真实源码树 + 真实词条文件（无服务） | 五个 `applies_to` 标签齐全且 zh/en 都有词条 | `tests/frontend/test_platform_settings_contract.py -k applies_to_labels` | ["uv","run","pytest","-q","tests/frontend/test_platform_settings_contract.py","-k","applies_to_labels"]| verified |
| B-08 | unit | 真实源码树（无服务） | 菜单十一项固定顺序；系统设置入口存在且 adminOnly；路由受 `RequireRole` 守卫；设置页无裸 axios/fetch | `tests/frontend/test_console_shell_contract.py` + `tests/frontend/test_platform_settings_contract.py` | ["uv","run","pytest","-q","tests/frontend/test_console_shell_contract.py","tests/frontend/test_platform_settings_contract.py"]| verified |

### Acceptance Evidence

#### 交付与 RED（先写测试、后实现）

新增 `tests/frontend/test_platform_settings_contract.py` 并改写 `tests/frontend/test_console_shell_contract.py`；把实现整体回退（`git stash` 实现文件 + 移出 `src/modules/settings/`，保留新测试）后真实失败原文：

```
12 failed, 1 passed in 0.19s
E   AssertionError: zh-CN missing menu keys: ['nav.settings']
FAILED tests/frontend/test_platform_settings_contract.py::test_settings_http_only_through_services_layer
FAILED tests/frontend/test_platform_settings_contract.py::test_backend_catalog_label_keys_are_translated_in_both_locales
FAILED tests/frontend/test_console_shell_contract.py::test_console_shell_menu_is_the_fixed_eleven_items
FAILED tests/frontend/test_console_shell_contract.py::test_console_shell_has_system_settings_entry
```

#### GREEN（实现后，实跑数字）

| 命令 | 结果 |
|------|------|
| `uv run pytest -q tests/frontend/test_platform_settings_contract.py tests/frontend/test_console_shell_contract.py` | **13 passed in 0.12s** |
| `uv run pytest -q tests/frontend/` | **274 passed in 0.27s** |
| verifier `harness-frontend#RULE-front-001`（api_client 契约 + api 机检 + i18n 机检 + typecheck） | 4 passed；`frontend api usage check OK`；`i18n keys OK: 854`；`tsc --noEmit` 无输出 |
| verifier `harness-ui#RULE-ui-001`（shell 契约 + ui style 契约 + build） | 9 passed；`✓ built in 2.57s` |
| verifier `harness-i18n#RULE-i18n-001`（foundation i18n + i18n 机检） | 6 passed；`i18n keys OK: 854` |

#### 断言位置（关键断言 → 文件/用例）

| 断言 | 位置 |
|------|------|
| HTTP 只经 services 层（组件/hook/页面无 axios/fetch/client import） | `tests/frontend/test_platform_settings_contract.py::test_settings_http_only_through_services_layer` |
| 字段控件元数据驱动、无默认值/范围字面量 | `::test_settings_field_control_reads_backend_metadata` |
| 页面不内联字段清单；四态齐（加载失败先于分组渲染返回） | `::test_settings_page_does_not_hardcode_field_or_group_lists`、`::test_settings_page_has_four_ui_states` |
| 保存携带 revision、冲突置位、字段错误映射 | `::test_save_uses_revision_and_maps_field_errors` |
| 五个 `applies_to` 标签 zh/en 齐（B-06） | `::test_applies_to_labels_cover_all_five_values` |
| 后端 catalog 每个 `label_key`/`unit_key`/readonly 词条两侧齐备 | `::test_backend_catalog_label_keys_are_translated_in_both_locales` |
| 后端 `applies_to` 取值都已被前端映射 | `::test_backend_applies_to_values_are_known_to_frontend` |
| 菜单 11 项固定顺序 + `adminOnly` 恰两项 | `tests/frontend/test_console_shell_contract.py::test_console_shell_menu_is_the_fixed_eleven_items` |
| 系统设置入口正向断言 + 路由受 `RequireRole role="ADMIN"` 守卫（B-08） | `::test_console_shell_has_system_settings_entry`、`test_platform_settings_contract.py::test_settings_route_is_role_guarded_and_menu_has_entry` |

#### 真实边界 / 事实同源

- 契约测试**直接 `import muad_console_platform.application.platform_settings_catalog`**，遍历 `build_groups(PlatformSettings())` 的每个 `label_key`/`unit_key` 与 `readonly_notes()`，断言 zh-CN/en-US 两侧词条齐备——不是人工枚举，实现加字段即红。
- i18n 词条由脚本追加，`check_frontend_i18n.py` 校验两侧键集齐平且 `t('字面量键')` 已定义。
- **事实文档/规范随代码事实改写**（FEAT-08 要求）：`docs/00-详细设计索引与设计基线.md`、`docs/README.md` 的「不提供系统设置菜单」表述；`harness-ui.md` 的「菜单固定十项」「Shell 不得出现系统设置入口」两条改为「十一项 + 两项 `adminOnly`（/users、/settings）+ 系统设置入口只能来自 `menu.ts` 且受 `RequireRole` 守卫」。另更新了 `tests/frontend/test_overview_routing_contract.py`（它钉着旧的「十项」事实）。

#### E2E（**只登记、未作为验收执行**）

登记：`e2e/playwright.settings.config.ts`（真实 Console + 真实构建产物 + `MUAD_API_TARGET`，`preview --strictPort`，钉浏览器时区）+ `e2e/tests/settings/settings.spec.ts`（S-01 / S-03 / E-11..E-14 / B-05）+ `tests/e2e/seed_platform_settings.py`（真实 `PlatformSettingsService` 种两个租户）。执行命令：`npm --prefix e2e test -- --config playwright.settings.config.ts`（即 `make acceptance-e2e DOMAIN=settings`）。终验归需求级 `verify-e2e`，本任务 E2E **只登记，不作为验收证据**。

**自检实跑**（仅自检、不计入验收证据）：先 `npm --prefix apps/console-platform/frontend run build`，再跑上条命令 → **4 passed, 3 failed**。
- 通过：**S-03 / E-13 / E-14 / B-05**。
- 失败：**S-01 / E-11 / E-12**——均卡在同一个保存 400（见下）。
- 自检期当场修掉两处**自身**缺陷（非产品缺陷）：① `e2e/tests/settings/` 嵌套目录使 `REPO = new URL('../..')` off-by-one，改 `../../..`；② Semi `Nav` 不透传 `id`，`#app-navigation` 选择器改 `getByRole('menu')`（修前 S-03 也红，修后 S-03 过）。

#### ⚠️ 发现的后端缺陷（阻断 S-01/E-11/E-12；本任务 E2E 因此只登记）

1. **位置与判定逻辑**：`apps/console-platform/backend/src/muad_console_platform/application/platform_settings_guard.py:20` 的 `SECRET_MARKERS` 含 `"password"`，`_scan`（同文件 `:37`）对**归一化（去 `_`/`-` 后小写）的键名做子串匹配** ⇒ schema 正常键 `auth.min_password_length` → `minpasswordlength` 命中 `password` ⇒ `reject_secret_keys`（`api/platform_settings.py:192`，**先于** parse/白名单）抛 `PLATFORM_SETTINGS_SECRET_REJECTED`。
2. **影响面**：任何**包含 `auth` 分组**的保存都被 400 拒绝，**包括后端设计 ADR-09 要求的整份文档保存**；`auth` 的 4 个字段在真实 PUT 下根本存不进去（E-15「改认证策略不重启」因此不可达）。
3. **为什么既有测试没抓到**：`tests/console_platform/test_auth_policy_settings.py` 是**直接 import `PlatformSettingsService` 写库**（绕过真实 PUT 守卫），而 `tests/console_platform/test_platform_settings_api.py` 的 PUT 用例只存**不含 `auth` 的部分文档**——两侧各让一步，中间漏掉。本地无 HTTP 直接复现：`find_secret_key(asdict(default_platform_settings())) == "auth.min_password_length"`。
4. **E2E 自检真实报错**（trace `*-trace.network` 实测）：请求体正确（`{"revision":1,"settings":{... "auth":{"min_password_length":12} ...}}`），`PUT /api/v1/platform-settings` → **400**，响应体 `{"code":"PLATFORM_SETTINGS_SECRET_REJECTED","msg":"不允许保存敏感配置项","data":null,...}`。
5. **依赖与归属**：S-01 / E-11 / E-12 三条 E2E 场景**依赖待补的守卫修复**（由 **TASK-015「敏感键守卫的业务键边界」**承接，带新场景：整份默认文档经真实 PUT 可保存 + 真敏感形状仍拒 + 未知键仍拒，并加结构性回归遍历 schema 全部叶子断言无误判）。这三条**本任务只登记、未作为验收执行**，终验归 `verify-e2e`。
- S-01: e2e_deferred — automated command e2e_deferred; run_id=7f7930852d624ced9a9e97283ef6d730 (confirmed_by: runner)
- S-03: e2e_deferred — automated command e2e_deferred; run_id=7f7930852d624ced9a9e97283ef6d730 (confirmed_by: runner)
- E-11: e2e_deferred — automated command e2e_deferred; run_id=7f7930852d624ced9a9e97283ef6d730 (confirmed_by: runner)
- E-12: e2e_deferred — automated command e2e_deferred; run_id=7f7930852d624ced9a9e97283ef6d730 (confirmed_by: runner)
- E-13: e2e_deferred — automated command e2e_deferred; run_id=7f7930852d624ced9a9e97283ef6d730 (confirmed_by: runner)
- E-14: e2e_deferred — automated command e2e_deferred; run_id=7f7930852d624ced9a9e97283ef6d730 (confirmed_by: runner)
- B-05: e2e_deferred — automated command e2e_deferred; run_id=7f7930852d624ced9a9e97283ef6d730 (confirmed_by: runner)
- B-06: verified — automated command passed; run_id=7f7930852d624ced9a9e97283ef6d730 (confirmed_by: runner)
- B-08: verified — automated command passed; run_id=7f7930852d624ced9a9e97283ef6d730 (confirmed_by: runner)

### Log
- [2026-10-05] created (draft)
- [2026-10-05] started
- [2026-10-05] completed (done)

---

## TASK-011: 重复默认源收敛与配置收口

- **Status**: done
- **Priority**: P1
- **Depends**: TASK-005, TASK-006, TASK-007, TASK-008, TASK-009, TASK-013
- **Source**: platform-settings.backend.design.md#3.1 方案选型, platform-settings.backend.design.md#4.4 数据迁移
- **Spec-Refs**:
- **Acceptance-Refs**: E-09

### Description

落地 ADR-05/06/08 与盘点 §必须收敛：工具结果三常量只留 schema 单一来源；历史预算两处合一；产物路径与默认租户各留一处；环境项在启动 settings 与 `.env.example` 中收口；盘点清单与机检同步。

### Checklist

- [x] 把 `RunService(settings_client=None)` 的**隐式** Null 默认去掉（改为必填参数，或让 Null 在 Run 创建路径上显式失败），消除「新建调用点忘记注入真 client ⇒ 静默按空设置跑」的隐患——生产目前只有 `api/deps.py` 一处装配，但默认值本身就是个陷阱（TASK-005 落地后复核提出）
- [x] `overridden_by_resources` 目前只统计压缩组（Agent 的 `runtime_config_json.budget.compaction`），其余分组恒 0——按 design 逐个补齐或明确降级为「不展示覆盖数」（二者选一并写进证据）
- [x] 工具结果默认收敛：删除 `TOOL_RESULT_ARTIFACT_BYTES`/`PREVIEW_HEAD_BYTES`/`PREVIEW_TAIL_BYTES` 与 `ToolResultSettings` 的重复默认，只留 schema 单一来源；非请求上下文显式传入
- [x] 历史预算收敛：`compaction.history_budget_messages` 与 `BudgetPolicy.max_messages` 用同一冻结值
- [x] 产物路径收敛：删除裸 `getenv` 的第二套默认（`/mnt/muad-artifacts`、`/var/cache/muad/skills`），统一经启动 settings
- [x] 默认租户收敛：CLI 的 `DEFAULT_TENANT` 改读 `SharedSettings.default_tenant_id`
- [x] **密码策略的两套来源（TASK-009 报备的真实冲突）**：`PasswordChangeRequest.new_password` 硬编码 `min_length=12`（`application/dto.py:20`），而 `auth.min_password_length` 的 schema 下界是 **8** ⇒ 把设置调到 8–11 时，页面上写 min=8、API 仍按 12 拦；这正是本需求要消灭的重复源。**建议解法**：DTO 的 `min_length` 退到 schema 绝对下界（8），策略下界改由服务层按平台设置校验、并**保持 422 `COMMON_VALIDATION_ERROR` 语义**——这样已归档需求 console-auth 的验收场景 S-04（9 位密码期望 422）仍然绿，同时设置真正生效。落地前先跑 `tests/acceptance/console_auth_flow/test_auth_acceptance.py` 确认 S-04 未被打穿
- [x] 环境项收口**复核**：改由业务设置接管的 12 个键按「谁切换谁摘除」已由前序任务一次性删除（`context_settings_cache_ttl_sec`→TASK-005、task 六项→TASK-006、`im_progress_interval_sec`→TASK-007、`artifact_retention_days`/`mcp_max_tools_per_server`/`default_locale`/`default_timezone`→TASK-013）——本任务逐条复核它们**确实不在启动 settings 与 `.env.example`**，且 `batch_platform_limit` 与 `im_progress_updates_per_second` 仍在环境
- [x] `.env.example` 补全为完整运维契约（覆盖余下全部环境类字段键名）
- [x] [E-09][integration] 机检：`.env.example` 键集与 `SharedSettings` 环境类字段一致；重复默认源已消除
- [x] 先写检查并记录 RED，再收敛
- [x] 运行验收命令并填写 Acceptance Evidence

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|--------|---------|--------------------|---------|----------------|---------|------|
| E-09 | integration | 真实源码树 + 真实 `.env.example` + 真实 `SharedSettings` 字段集 | 重复默认源消除；12 个键已从启动 settings 与示例移除；示例覆盖余下全部环境类字段 | `tests/test_configuration_convergence.py` | ["uv","run","pytest","-q","tests/test_configuration_convergence.py"] | verified |

### Acceptance Evidence

#### RED（先写检查，实现前真实失败）

新增 `tests/test_configuration_convergence.py` 后、收敛前跑契约命令，真实输出（摘要）：

```
$ uv run pytest -q tests/test_configuration_convergence.py
FAILED tests/test_configuration_convergence.py::test_env_example_is_complete_operations_contract
FAILED tests/test_configuration_convergence.py::test_retained_resource_budgets_stay_in_environment
FAILED tests/test_configuration_convergence.py::test_tool_result_defaults_have_single_source
FAILED tests/test_configuration_convergence.py::test_budget_policy_default_derives_from_compaction_schema
FAILED tests/test_configuration_convergence.py::test_run_service_requires_settings_client
FAILED tests/test_configuration_convergence.py::test_no_bare_artifact_path_getenv_defaults
FAILED tests/test_configuration_convergence.py::test_password_dto_floor_matches_schema_lower_bound
FAILED tests/test_configuration_convergence.py::test_cli_default_tenant_reads_shared_settings
8 failed, 1 passed in 0.91s
```

失败原文（关键三条）：
- `E AssertionError: .../bootstrap/artifacts.py 仍带第二套产物路径默认 /mnt/muad-artifacts`（`/var/cache/muad/skills` 同理）；
- `E pydantic ... String should have at least 12 characters [type=string_too_short, input_value='aaaaaaaa']` —— DTO 仍硬编码 12；
- `E assert True = isinstance(<ast.Constant object>, <class 'ast.Constant'>)` —— `BudgetPolicy.max_messages` 仍是硬编码默认 `40`；
- `.env.example` 键集缺 20 个 `SharedSettings` 环境类字段（如 `DEFAULT_TENANT_ID`/`BATCH_PLATFORM_LIMIT`/`RUN_LEASE_SEC`）。

#### GREEN（命令与数字）

```
$ uv run pytest -q tests/test_configuration_convergence.py
9 passed in 0.86s

$ uv run pytest -q tests/test_configuration_inventory.py
3 passed in 0.64s        # 盘点 CSV 按事实同步（只改 path/line/value，`category` 未动）

$ uv run pytest -q tests/agent_runtime tests/agent_core tests/agent_worker tests/console_platform tests/gateway tests/test_settings.py
1226 passed, 3 warnings in 116.80s

$ uv run ruff check apps packages tests
All checks passed!

$ uv run pytest -q tests/acceptance/console_auth_flow/test_auth_acceptance.py
2 failed, 21 passed      # S-04 两条腿全绿；红的是 S-02/B-02（既有失败，见下）
```

#### 每条断言的断言位置

| 收敛项 | 断言用例 | 位置 |
|--------|---------|------|
| `.env.example` 键集 == `SharedSettings` 环境类字段 | `test_env_example_is_complete_operations_contract` | `tests/test_configuration_convergence.py:87-93` |
| 12 个业务化键已离开 settings 与示例 | `test_migrated_environment_keys_are_gone` | `:96-102` |
| `batch_platform_limit`/`im_progress_updates_per_second` 仍在环境 | `test_retained_resource_budgets_stay_in_environment` | `:105-110` |
| 工具结果三常量已删（全仓 AST 扫描） | `test_tool_result_defaults_have_single_source` | `:113-116` |
| `BudgetPolicy.max_messages` 派生自 compaction schema（非字面量） | `test_budget_policy_default_derives_from_compaction_schema` | `:133-143` |
| `RunService.settings_client` 必填（签名无默认值） | `test_run_service_requires_settings_client` | `:146-150` |
| 裸 `getenv` 产物路径默认已删 | `test_no_bare_artifact_path_getenv_defaults` | `:153-159` |
| 口令 DTO 只守下界 8 | `test_password_dto_floor_matches_schema_lower_bound` | `:162-172` |
| CLI `DEFAULT_TENANT` 非字面量 | `test_cli_default_tenant_reads_shared_settings` | `:175-189` |

#### 真实边界证据（不 mock）
- 真实 `.env.example`：显式赋值键 **13 → 33**，与 `SharedSettings.model_fields` 的 33 个字段一一对应（键名 = 字段名大写）；
- 真实源码 AST：扫描 `apps/`+`packages/` 全部 `*.py` 的模块级/嵌套赋值与契约类字段（无 stub、无 mock、无 DB）；
- 真实 PG/Redis：上表 1226 条用例经真实 PostgreSQL + Redis 全绿（含 E-07 的 `overridden_by_resources` 断言、E-15 的认证策略断言）。

#### 收敛落点（逐条）
1. `RunService.__init__` 的 `settings_client` 改**必填**（`run_service.py:439`），移除隐式 `NullPlatformSettingsClient` 默认与 import；直构点显式传 `NullPlatformSettingsClient()`（`tests/agent_runtime/{test_run_service,test_cancel_hint}.py`）；生产唯一装配 `api/deps.py:112`。
2. `overridden_by_resources` **补齐**（取舍见下）：`count_resource_overrides`（`platform_settings_catalog.py`）+ `build_groups(settings, *, overrides=...)`。
3. 删除 `TOOL_RESULT_ARTIFACT_BYTES`/`PREVIEW_HEAD_BYTES`/`PREVIEW_TAIL_BYTES`（`tool_results.py`）；`ArtifactResultWriter` 的预览参数与 `ArchiveToolSet.receipt_limit_bytes` 改必填（**不设第二套默认**），schema `ToolResultSettings` 是唯一来源。
4. `BudgetPolicy.max_messages` 默认改 `default_compaction_settings().history_budget_messages`（与 `compaction.history_budget_messages` 同一冻结值）。
5. 删除死模块 `apps/agent-runtime/src/muad_agent_runtime/bootstrap/{__init__,artifacts}.py`（全仓零引用，只存裸 `getenv` 第二套默认；产物路径统一经 `main.py` 的启动 settings）。worker 的同名模块本就用 `SharedSettings()`，保留。
6. CLI `DEFAULT_TENANT = SharedSettings().default_tenant_id`（`cli.py:20`）。
7. 口令：新增 schema 绝对下界常量 `MIN_PASSWORD_LENGTH_FLOOR = 8`（`contracts/platform_settings.py`），`PasswordChangeRequest.new_password` 与 `AccountCreateRequest.password` 的 `min_length` 退到它；`auth_service.change_password` 的策略校验改抛 `COMMON_VALIDATION_ERROR`（422）。`create_account` 的策略校验**保持** `COMMON_BAD_REQUEST`（400）——同需求的 E-15 明确断言「400 业务校验」，两条已归档场景各自保持原语义。
8. 12 个环境键复核：均不在 `SharedSettings` 与 `.env.example`；`batch_platform_limit` / `im_progress_updates_per_second` 仍在。
9. `.env.example` 补全为完整运维契约（33 键）。

#### 第 2 项取舍：**补齐**（非降级）

- 降级选项要求「不展示覆盖数」，而前端 `OverrideBadge`（`apps/console-platform/frontend/src/modules/settings/components/OverrideBadge.tsx:13-26`）对 `count=0` **无条件**渲染「无资源覆盖，全部使用平台默认」——不改前端无法真正隐藏，而本任务明令不碰前端；且存在真实覆盖时显示 0 是**假陈述**。
- 覆盖载体只有 Agent 的 `runtime_config_json`：`budget.compaction`（compaction）、`max_turns`/`max_tool_calls`/`deadline_ms`/`max_model_retries`（agent）、`memory_write`（memory）。三组现按真实行数统计；`task`/`artifact`/`auth`/`locale`/`im`/`mcp` 无任何资源表存逐资源覆盖，0 是**结构事实**而非占位（已写进模块 docstring 与函数 docstring）。
- E-07（`tests/agent_runtime/test_platform_settings_source.py::test_e07_...`）断言 compaction 组如实返回 1，仍绿。

#### 第 7 项：是否打穿 console-auth S-04

**未打穿。** S-04 两条腿（改密轮换成功；9 位 `too-short` → 422 `COMMON_VALIDATION_ERROR`）在 `uv run pytest -q tests/acceptance/console_auth_flow/test_auth_acceptance.py` 中均通过。同文件另有 2 条失败（`test_s02_sliding_renewal_extends_expiry_when_under_half`、`test_b02_six_hour_boundary_renews_only_below`，会话滑动续期）——已用 `git stash` 在**未改动的 HEAD** 上复跑，**同样失败**，属既有缺陷、与本任务无关；按纪律未放宽 S-04 期望、未改他人验收。
- E-09: verified — automated command passed; run_id=b5fcf3ada1734893838ed1a4d75728ea (confirmed_by: runner)

### Log
- [2026-10-05] created (draft)
- [2026-10-05] started
- [2026-10-05] completed (done)

---
## TASK-012: 收口清单与需求级终验

- **Status**: draft
- **Priority**: P0
- **Depends**: TASK-001..TASK-017
- **Source**: platform-settings.backend.design.md#2.5 验收条件, platform-settings.frontend.design.md#2.4 验收条件
- **Spec-Refs**: harness-test#RULE-test-001
- **Acceptance-Refs**: E-18

### Description

建立收口清单 `tests/platform_settings_inventory.py`（参照 `tests/context_compaction_inventory.py`），做本需求**自身**的三方闭环校验，并跑需求级终验（`harness-test` verifier：分层验收链 + 前端构建 + e2e）。

### Checklist

- [ ] 收口清单 `tests/platform_settings_inventory.py`：**每个 TASK 的 Checklist 全部勾选**、覆盖表每行唯一负责人且终态、manifest 与覆盖表同 ID/owner/命令并比对 `level`/`boundary`/`cwd`、每个 TASK 契约表**每一行**终态（含收口任务自身）、证据表无占位行、`-k` 令牌在真实用例名里命中、登记路径真实存在
- [ ] 结构性 RED + ≥4 类扰动取证（改状态 / 删证据行 / 伪造用例名或命令 / 改 manifest 字段），逐字节还原后复绿
- [ ] [E-18][integration] 覆盖收口清单自身的校验；真实边界：**真实任务文档与 manifest**（不豁免收口任务自身）
- [ ] [harness-test#RULE-test-001][review] 运行分层验收链：`uv run pytest -q tests/acceptance && npm --prefix apps/console-platform/frontend run build && npm --prefix e2e test`；真实边界：**真实 PG/Redis + 真实构建产物 + 真实浏览器**
- [ ] **如实披露两处已知覆盖缺口（TASK-014 报备，不得在收尾证据里含糊）**：① `im.progress_interval_sec` 在 **E2E 层没有断言**——B-03 只在单元层钉取值；E2E 里 `已执行 00:01` 来自相位切换帧（强制）而非 1s 节拍帧，去掉种下的覆盖后扰动用例照样通过（实测）；② `dfx` 的批次并发 E2E 语义变了：种下的 `task.batch_max_concurrency` 现在是唯一约束（原先计划里也请求 `max_concurrency`，两者取小后恰好相等，等于在验计划请求而不是平台上限），「计划请求低于平台上限」这一维改由 `tests/agent_worker/test_batch_fanout.py` 单测覆盖
- [ ] 需求级 `verify-e2e`：`cf_acceptance_runner.py --manifest … --include-e2e --write-evidence`，28/28 场景全过
- [ ] 先写清单并记录 RED（清单缺失时登记的 argv 必须失败），再补齐
- [ ] verifier `harness-test#RULE-test-001`：`uv run pytest -q tests/acceptance && npm --prefix apps/console-platform/frontend run build && npm --prefix e2e test`（真实边界：真实 PG/Redis + 真实构建产物 + 真实浏览器；acceptance 单跑约 1064s，跑前须停 dev 服务并查残留进程）
- [ ] **规范沉淀**：把本次引入的事实性约束写进对应 live spec 的 `## Conventions`（已知至少三条：① `actor_user_id` 一类**操作者引用即使同 Owner Schema 也用逻辑引用**、不建物理 FK（先例 `ConfigAuditLog.actor_user_id`，与 `RULE-data-001` 字面口径的张力在此写明）；② 平台设置版本行的 append-only 口径（`is_deleted` 恒 `false`、当前版本 = 该租户 `max(revision)`、乐观并发由 partial unique 兜底、回滚产生新版本）；③ 平台业务默认只在**业务操作边界**取一次快照，执行中的 Run/Task 用冻结快照）；④ live spec 的旧口径必须改写：`im/harness-im.md:83,89`（节拍来源写成 `IM_PROGRESS_INTERVAL_SEC` 环境变量，且把「验收栈注入 1s」当成 ✅ 示例）——**`mcp/harness-mcp.md:66` 已由 TASK-009 顺手改掉，不必重复**；⑤ `model/harness-model.md` 的重试/退避 Conventions（`model_gateway.py:105-125` 的行号与「超过 `max_retries`」的表述）在 ADR-07 之后要复核——**语义变了就改语义，只是行号漂移就只修引用**，别把仍然正确的口径改坏
- [ ] 运行验收命令并填写 Acceptance Evidence

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|--------|---------|--------------------|---------|----------------|---------|------|
| E-18 | integration | 真实任务文档与 manifest（收口清单交叉核对） | 覆盖表↔契约表↔证据表三方闭环；manifest 同 ID/owner/命令且 level/boundary/cwd 一致；`-k` 命中；不豁免收口任务自身 | `tests/platform_settings_inventory.py` | ["uv","run","pytest","-q","tests/platform_settings_inventory.py"] | planned |
| RULE-test-001 | review | 真实 PG/Redis + 真实前端构建产物 + 真实浏览器 | 分层验收链三条腿全过；e2e 覆盖 S-01/S-02/S-03/E-11..E-14/B-05 | `tests/acceptance` + `apps/console-platform/frontend` + `e2e` | ["uv","run","pytest","-q","tests/acceptance"] | planned |

### Acceptance Evidence

> 收口清单与需求级终验证据由本任务在收尾期登记；RULE 行按 owner 回填。

### Log
- [2026-10-05] created (draft)

---

## TASK-013: 执行默认接入（agent / memory / artifact 分组）

- **Status**: done
- **Priority**: P0
- **Depends**: TASK-005
, TASK-006, TASK-007
- **Source**: platform-settings.backend.design.md#2.3 功能方案, platform-settings.backend.design.md#3.2 架构设计
- **Spec-Refs**:
- **Acceptance-Refs**: E-20

### Description

把剩下的 9 个叶子接到既有消费点：`agent.max_turns`/`max_tool_calls`（Agent 执行预算）、`memory.write_enabled`/`max_injected_memories`/`max_injected_bytes`/`max_recall_bytes`/`recall_default_limit`、`artifact.retention_days`/`max_archive_files`/`cleanup_batch_size`。取值一律来自本 Run 冻结的 execution snapshot 或当次操作边界取到的快照，**不改写存量数据**。

### Checklist

- [x] `agent.max_turns`/`agent.max_tool_calls`：替换 `AgentPolicy` 默认，Agent 显式 `runtime_config` 覆盖仍优先。**接口缝已由 TASK-008 留好**：`ExecutorRequest.model_budget` + `executor.agent_policy_for()` 目前只承接 `deadline_ms`/`max_model_retries`——把注入基值换成含四叶的载体即可，**不要新增取数方式或缓存**
- [x] `memory.write_enabled` 替换 `memory_tools` 的「未显式配置时默认 True」；`max_injected_memories`/`max_injected_bytes` 接入 `context_builder` 的记忆注入预算；`max_recall_bytes`/`recall_default_limit` 接入 `memory_tools`（`recall_default_limit ≤ RECALL_MAX_LIMIT`，上界单一来源见 TASK-011 收敛）
- [x] `artifact.max_archive_files` 接入 `archive_tools`；`artifact.retention_days`/`artifact.cleanup_batch_size` 接入 Console 清理入口（CLI 本次操作覆盖仍优先）
- [x] 全部取值来自冻结快照或操作边界快照，**不在每轮模型调用/每次工具执行里重新读取**
- [x] Runtime 侧 `locale.default_timezone`（`executor.py` 装配 `TimeToolSet` 的 zone）改读冻结快照
- [x] 一次性切换收口：三个消费方（Worker 投递文案 / Gateway 回复渲染 / 本任务的 `TimeToolSet`）都切换后，删除 `SharedSettings.default_locale` 与 `default_timezone`、`.env.example` 同步移除；同批删掉本任务接管的 `artifact_retention_days`、`mcp_max_tools_per_server`；**部署面也要摘**：`.env.example:4` 的 `DEFAULT_LOCALE` 与 `deploy/k8s/base/configmap.yaml:7` 的 `DEFAULT_LOCALE`（只改示例不改 ConfigMap，运维会以为改它还有效）；同批删掉本任务接管的 `artifact_retention_days`（`mcp_max_tools_per_server` 归 TASK-009，不要重复摘）
- [x] `apps/console-platform/.../cli.py:61,192` 的 `cleanup-artifacts --retention-days` 默认值改从平台设置取（CLI 本次操作覆盖仍优先）
- [x] [E-20][integration] 覆盖：改这 9 个叶子后新 Run 与新一次清理使用新值；既有 Run/Task 行与已落库记忆不被改写；真实边界：**真实 PostgreSQL + 真实 Runtime 装配 + 真实 Console 清理入口**（不 mock）
- [x] 先写测试并记录 RED，再实现
- [x] 同步 `docs/configuration-inventory.csv` 与机检期望（`MAX_INJECTED_MEMORIES`/`MAX_ARCHIVE_FILES` 等常量改由设置提供）
- [x] 运行验收命令并填写 Acceptance Evidence

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|--------|---------|--------------------|---------|----------------|---------|------|
| E-20 | integration | 真实 PostgreSQL + 真实 Runtime 装配 + 真实 Console 清理入口 | 9 个叶子对新 Run / 新清理生效；存量 Run/Task/记忆不被改写 | `tests/agent_runtime/test_execution_defaults_settings.py` | ["uv","run","pytest","-q","tests/agent_runtime/test_execution_defaults_settings.py"] | verified |

### Acceptance Evidence

#### RED（先写测试，实现前失败原文）

在实现前（实现改动 `git stash` 掉后）跑契约命令，真实失败原文：

```
ERROR collecting tests/agent_runtime/test_execution_defaults_settings.py
ImportError while importing test module '.../tests/agent_runtime/test_execution_defaults_settings.py'.
tests/agent_runtime/test_execution_defaults_settings.py:47: in <module>
    from muad_agent_runtime.application.run_service import (
E   ImportError: cannot import name 'execution_defaults_from_platform' from 'muad_agent_runtime.application.run_service'
!!!!!!!!!!!!!!!!!!!! Interrupted: 1 error during collection !!!!!!!!!!!!!!!!!!!!
1 error in 0.07s
```

（`ExecutionDefaults`/`execution_defaults_of`/`execution_defaults_from_platform` 与四叶冻结载体在实现前不存在。）

#### GREEN（实现后）

- `uv run pytest -q tests/agent_runtime/test_execution_defaults_settings.py` → **7 passed in 0.61s**
- `uv run pytest -q tests/agent_runtime` → **364 passed in 11.89s**
- `uv run pytest -q tests/agent_core` → **109 passed in 7.77s**
- `uv run pytest -q tests/console_platform` → **168 passed in 19.32s**
- `uv run pytest -q tests/agent_worker` → **251 passed**；`uv run pytest -q tests/gateway` → **323 passed**
- `uv run pytest -q tests/test_configuration_inventory.py` → **3 passed**（CSV 已按源码重算）
- 回归门禁：`uv run mypy apps packages` → **Success: no issues found in 311 source files**；`uv run ruff check apps/ packages/ tests/` → **All checks passed**
- harness-snapshot verifier：`uv run pytest -q tests/agent_runtime/test_snapshot_freeze.py tests/agent_runtime/test_run_reaper.py && uv run pytest -q tests/agent_runtime -k "executor or resolve"` → **4 passed / 24 passed**

#### 断言位置（关键断言逐条）

| 断言 | 位置 |
|------|------|
| 新 Run 冻结 9 叶（`max_turns`/`max_tool_calls`/`deadline_ms`/`max_model_retries`/`memory.*` 5 叶/`max_archive_files`/`default_timezone`）；`retention_days`/`cleanup_batch_size` **不进** 快照 | `tests/agent_runtime/test_execution_defaults_settings.py::test_e20_new_run_freezes_nine_execution_leaves` |
| agent 四叶经真实 `agent_policy_for` 成为执行期 `AgentPolicy` 基值 | `::test_e20_agent_policy_applies_frozen_defaults` |
| 真实 `build_registry`：`create_archive` schema `maxItems` 与执行期拒收同值；`memory.write_enabled=false` ⇒ `remember` 不注册、`recall` 仍在；`current_time` 输出含新 IANA 时区 | `::test_e20_registry_uses_frozen_artifact_memory_and_locale` |
| `recall_default_limit` 是缺省条数；`max_recall_bytes` 触顶压缩条数 | `::test_e20_recall_uses_frozen_limit_and_byte_cap` |
| 真实 `DbBackedContextBuilder.load_history` 用冻结 `memory.max_injected_memories` 限注入条数 | `::test_e20_memory_injection_caps_from_frozen_policy` |
| 既有 Run 的 `policy_json` 逐键不动、已落库记忆行逐列不变；新 Run 才用新值 | `::test_e20_existing_run_and_memories_not_rewritten` |
| 真实 `_cleanup_artifacts`：保留期取自 `artifact.retention_days`、批大小取自 `artifact.cleanup_batch_size`（各只清 1 个、未过期不动） | `::test_e20_console_cleanup_uses_current_settings` |

#### 冻结口径（哪些进 `policy_json`，哪些是操作边界取）

- **进 `policy_json`（Run 侧冻结，resume 读回，配置变更只影响后续新 Run）**：`max_turns`、`max_tool_calls`、`deadline_ms`、`max_model_retries`（顶层）；`memory.{write_enabled,max_injected_memories,max_injected_bytes,max_recall_bytes,recall_default_limit}`；`artifact.max_archive_files`；`locale.default_timezone`；外加既有 `compaction`。
- **操作边界取（不冻结，清理不是 Run）**：`artifact.retention_days`、`artifact.cleanup_batch_size`——在 `cli._cleanup_artifacts` 每次执行时经真实 Console `PlatformSettingsService.read_current` 取当前值，CLI `--retention-days`/`--limit` 本次覆盖仍优先。
- 执行期不再重新读设置：`policy_json` 只在 `_create_run` 边界取一次平台快照并冻结；`build_registry`/`load_history` 只读冻结值（NFR-PERF-01）。

#### 真实边界与收口

- 真实 PostgreSQL：`control.platform_setting`（经真实 Console `PlatformSettingsService` 读写）、`runtime.runtime_snapshot.policy_json`、`runtime.user_memory`、`runtime.artifact`。
- 真实 Runtime 装配：Run 经真实 `RunService.start` 创建；执行期经真实 `build_registry`（`MemoryToolSet`/`ArchiveToolSet`/`TimeToolSet`）与 `DbBackedContextBuilder.load_history`（仅替换模型调用这一环）。
- 真实 Console 清理入口：`muad_console_platform.cli._cleanup_artifacts`（真命令协程 + 真 PG + 真文件系统 mtime）。
- 一次性切换收口：`SharedSettings` 删除 `default_locale`/`default_timezone`/`artifact_retention_days`；`.env.example` 与 `deploy/k8s/base/configmap.yaml` 摘除 `DEFAULT_LOCALE`；api-kit 的 API 错误文案兜底语言改回框架常量 `zh-CN`（`locale.default_locale` 的业务消费方是 Worker 投递/Gateway 渲染，TASK-006/007 已切换）。`mcp_max_tools_per_server` 归 TASK-009，本次未动。
- 常量收敛：删除 app 侧 `MAX_INJECTED_MEMORIES`/`MAX_INJECTED_BYTES`/`RECALL_DEFAULT_LIMIT`/`MAX_RECALL_BYTES`/`MAX_ARCHIVE_FILES`/`DEFAULT_CLEANUP_LIMIT`，默认值改从 `muad_contracts.platform_settings` schema 取；`RECALL_MAX_LIMIT` 单一来源改为 contracts（app 侧不再复制）；`AgentPolicy` 的 `max_turns`/`max_tool_calls` 默认改由 `budget.DEFAULT_MAX_TURNS`/`DEFAULT_MAX_TOOL_CALLS`（源自 `AgentSettings`）提供。`docs/configuration-inventory.csv` 按源码重算同步。
- E-20: verified — automated command passed; run_id=644f13d46cd046698b93fbd2d624033b (confirmed_by: runner)

### Log
- [2026-10-05] created (draft)
- [2026-10-05] started
- [2026-10-05] completed (done)

---

## TASK-014: 验收栈按租户种平台设置

- **Status**: done
- **Priority**: P0
- **Depends**: TASK-006, TASK-007, TASK-013
- **Source**: platform-settings.backend.design.md#2.5 验收条件, platform-settings.backend.design.md#3.2 架构设计
- **Spec-Refs**:
- **Acceptance-Refs**: E-21

### Description

`task.*` 与 `locale.*` 等键从启动 settings 删除后，各 acceptance 栈**原先靠 env 注入**这些键的做法静默失效：`tests/acceptance/dfx/test_dfx_routing.py` 的并发上限断言（`BATCH_MAX_CONCURRENCY = 2`，现由平台设置默认 8 驱动）会直接挂，`test_dfx_delivery.py` 的退避窗口（按 `DELIVERY_BACKOFF_BASE_SEC` 算）同样。正确形态是**栈启动时按租户种一行 `control.platform_setting`**，与生产同构。

### Checklist

- [x] 全仓扫一遍：`grep -rn "<12 个已删键>" tests/acceptance e2e`，列出每一处 env 注入/常量来源；已知一处：`tests/acceptance/dfx/test_dfx_model_recovery.py:9` 的 docstring 仍引用已删符号 `DEFAULT_RETRY_BASE_SEC`（TASK-008 报备，超其范围未改）
- [x] 各 acceptance 栈（`dfx` / `task_schedule` / `im_gateway` 及扫出的其他栈）在启动时按**该栈的租户**种一行 `control.platform_setting`，值取原先 env 注入的非默认值；删除这些栈里的 env 注入。已扫出的具体落点：`tests/acceptance/im_gateway/environment.py:142`（`IM_PROGRESS_INTERVAL_SEC=1`；该栈 90s 超时对新窗口 80s 很紧）、`tests/acceptance/dfx/environment.py`（`DELIVERY_BACKOFF_BASE_SEC`）、`tests/acceptance/task_schedule/environment.py:46,423`（`DELIVERY_BACKOFF_BASE_SEC=2`）、`tests/acceptance/dfx/test_dfx_routing.py:112`（`BATCH_MAX_CONCURRENCY=2`）
- [x] `tests/acceptance/dfx/test_dfx_fault_matrix.py` 的 `TASK_MAX_ATTEMPTS=1` 臂：改由平台设置表达（同一租户不同 Worker 进程的覆盖）
- [x] 栈内断言改为从**种下的设置**推导期望值，不再依赖 env 常量
- [x] [E-21][integration] 覆盖：栈按种下的设置观察到退避窗口 / 并发上限 / 尝试次数；env 注入路径不存在；真实边界：**真实 acceptance 栈（真实 Console API + 真实 PG + 真实 Worker 进程）**
- [x] 先跑一次记录 RED（改前栈内断言会失败/失真），再改
- [x] 运行验收命令并填写 Acceptance Evidence

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|--------|---------|--------------------|---------|----------------|---------|------|
| E-21 | integration | 真实 acceptance 栈（真实 Console API + 真实 PostgreSQL + 真实 Worker 进程） | 栈内用例按种下的设置观察行为；已删键的 env 注入不复存在 | `tests/acceptance/dfx` + 扫出的其他栈 | ["uv","run","pytest","-q","tests/acceptance/dfx"] | verified |

### Acceptance Evidence

**扫面（`grep -rn` 12 个已删键，`tests/acceptance` + `e2e`）**：命中且为 env 注入/常量来源的全部 4 处（其余命中的是业务设置文档自身的 `locale.*` 键与 Console `default_locale=` 参数，非 env 注入）：

- `tests/acceptance/im_gateway/environment.py:142` `IM_PROGRESS_INTERVAL_SEC=1`、`:205` `DELIVERY_BACKOFF_BASE_SEC="1"`
- `tests/acceptance/task_schedule/environment.py:46` `DELIVERY_BACKOFF_BASE_SEC = 2`、`:423` 同名 env 注入
- `tests/acceptance/dfx/environment.py:76` `DELIVERY_BACKOFF_BASE_SEC = 2`、`:585` 同名 env 注入
- `tests/acceptance/dfx/test_dfx_fault_matrix.py:721` `TASK_MAX_ATTEMPTS="1"`（单进程 env 覆盖）
- `tests/acceptance/dfx/test_dfx_routing.py:112` `BATCH_MAX_CONCURRENCY = 2`（写死常量）
- `tests/acceptance/dfx/test_dfx_model_recovery.py:9` docstring 引用已删符号 `DEFAULT_RETRY_BASE_SEC`

`e2e/`（Playwright）未命中任何已删键的注入。

**改前 RED**（`uv run pytest -q tests/acceptance/dfx`，改前工作树）：**2 failed, 52 passed in 404.29s**

```
FAILED tests/acceptance/dfx/test_dfx_delivery.py::test_e06_backoff_sequence_then_success
FAILED tests/acceptance/dfx/test_dfx_fault_matrix.py::test_e03_artifact_store_unavailable_keeps_ready_and_fails_new_writes

E   AssertionError: 第 1 次失败后的退避间隔 10.258559s 不在 [4.0, 9.0]
E   assert 10.258559 <= (4.0 + 5.0)                        # test_dfx_delivery.py:455
E   assert 3 == 1                                          # test_dfx_fault_matrix.py:731（臂 B 的 task 行 max_attempts）
```

根因：`DELIVERY_BACKOFF_BASE_SEC` / `TASK_MAX_ATTEMPTS` 的 env 注入在平台设置接管后**已静默失效**，Worker 退回 schema 默认 —— 退避 base 5（窗口 5×2=10）而断言按注入的 2（窗口 2×2=4）；任务尝试上限 3 而断言 1。

`test_dfx_routing.py` 的批量并发臂改前**为绿**（如实说明）：批量脚本在**计划里请求** `max_concurrency=2`，`min(计划 2, 平台默认 8, 上限 16) = 2`，恰好与断言一致 —— 即该断言此前观测的是**计划请求值**、不是平台上限，平台设置这条缝被盖住。本次删除计划里的 `max_concurrency`，让种下的 `task.batch_max_concurrency=2` 成为**唯一约束**，断言才真正观测平台设置。

**改后 GREEN（逐栈实跑，均独占 `muad_acc_<uuid>` 空库 + 独立 Redis，串行无并发 pytest）**：

| 栈 | 命令 | 结果 |
|----|------|------|
| **dfx**（E-21 契约命令，逐字对齐） | `uv run pytest -q tests/acceptance/dfx` | **54 passed in 326.19s** |
| task_schedule | `uv run pytest -q tests/acceptance/task_schedule` | **24 passed in 61.54s** |
| im_gateway | `uv run pytest -q tests/acceptance/im_gateway` | **72 passed, 2 warnings in 430.94s** |

**种设置的形态**：新增 `tests/acceptance/datastores.seed_platform_settings(database_url, tenant_id, settings)`，走原始 `INSERT INTO control.platform_setting (tenant_id, revision=1, settings_json) VALUES (…, $3::jsonb)` —— 与生产保存路径写同一张表、读取侧读同一份 schema。`settings` 是**部分文档**：只给需要非默认的键，其余由 schema 默认补全（已实测 `parse_platform_settings({'task': {...}})` 能补全为完整设置，故**未改 contracts**）。各栈在启动夹具里 `cleanup → seed_control → seed_platform_settings`，在服务子进程起来**之前**种下：

- `dfx`（租户 `dfx-reliability-*`）：`{"task": {"max_attempts": 1, "delivery_backoff_base_sec": 2}}`
- `task_schedule`（`e2e-task-schedule`）：`{"task": {"delivery_backoff_base_sec": 2, "batch_max_concurrency": 2}}`
- `im_gateway`（`e2e-im-gateway`）：`{"im": {"progress_interval_sec": 1}, "task": {"delivery_backoff_base_sec": 1}}`

**清理**：`task_schedule.environment.CONTROL_CLEANUP` 首条加 `DELETE FROM control.platform_setting WHERE tenant_id = :t`（task_schedule / dfx / im_gateway 三栈共用同一批清理语句，收尾清零）。

**env 注入已删除**：dfx 与 task_schedule 的 `DELIVERY_BACKOFF_BASE_SEC`、im_gateway 的 `IM_PROGRESS_INTERVAL_SEC` 与 `DELIVERY_BACKOFF_BASE_SEC` 均已从 `environment.py` 的 env 字典与相关常量中移除；仍属环境的键（`BATCH_PLATFORM_LIMIT`、`IM_PROGRESS_UPDATES_PER_SECOND`、`DELIVERY_POLL_INTERVAL_SEC`、`TASK_LEASE_SEC`、`TASK_HEARTBEAT_SEC` 等）保留。

**断言位置（从种下的设置推导期望值）**：
- 退避窗口：`tests/acceptance/dfx/test_dfx_delivery.py:78`（`_backoff_base_sec()` → `PLATFORM_SETTINGS.task.delivery_backoff_base_sec`）与 `:461`（窗口 `base * 2**index`）
- 尝试次数：`tests/acceptance/dfx/test_dfx_fault_matrix.py:735`（`assert row["max_attempts"] == PLATFORM_SETTINGS.task.max_attempts`）
- 并发上限：`tests/acceptance/dfx/test_dfx_routing.py:116`（`BATCH_MAX_CONCURRENCY = PLATFORM_SETTINGS.task.batch_max_concurrency`）
- 投递尝试上限（im_gateway）：`tests/acceptance/im_gateway/test_worker_delivery.py:178`

**真实边界（E-21）**：三栈均为真实 uvicorn 子进程（Console / Runtime / Worker / Gateway）+ 真实 PostgreSQL（迁移到 head 的独立空库）+ 真实 Redis + 真实本地探针，无 mock。

**扰动取证（作用于隔离数据，验证「种下的设置」确为 load-bearing）**：对 im_gateway 做两次扰动（改种值 → 单跑 → 还原，收尾 `git diff` 已确认还原）：
- 去掉 `im.progress_interval_sec` 覆盖（回 schema 默认 5.0）单跑 `tests/acceptance/im_gateway/test_execution_progress.py` → **1 passed**：该用例命中的 `已执行 00:01` 是**阶段变化帧**（force 帧按真实 elapsed 渲染），非 1s tick 帧，故 im 节拍未被现有断言钉住 —— 如实登记：本次是把该键恢复到生产同构形态，非某条断言的直接驱动。
- 去掉 `task.delivery_backoff_base_sec=1` 覆盖（回生产默认 5）单跑 `tests/acceptance/im_gateway/test_worker_delivery.py::test_b127_failure_retries_then_exhausts_without_swallowing_fact` → 用例**耗时 97s**（该用例等待上限 `WAIT_TIMEOUT_SEC=90`，退避窗口从 16s 退化到 80s，逼近超时）；种回 `=1` 后随整栈 72 passed 通过。
- E-21: verified — automated command passed; run_id=9a20db961ec64ce39510440d4660c121 (confirmed_by: runner)

### Log
- [2026-10-05] created (draft)
- [2026-10-05] started
- [2026-10-05] completed (done)

---

## TASK-015: 敏感键守卫的业务键边界

- **Status**: done
- **Priority**: P0
- **Depends**: TASK-004
- **Source**: platform-settings.backend.design.md#3.4 接口设计, platform-settings.backend.design.md#3.5 质量实现方案
- **Spec-Refs**:
- **Acceptance-Refs**: E-22

### Description

TASK-010 的 E2E 照出的真缺陷：`application/platform_settings_guard.py` 的 `SECRET_MARKERS` 对**归一化键名做子串匹配**，而 `_normalize("min_password_length") == "minpasswordlength"` 含 `password` ⇒ 命中，且在 parse/白名单**之前**抛 `PLATFORM_SETTINGS_SECRET_REJECTED`。后果：**含 `auth` 分组的任何保存都 400**，包括 ADR-09 规定的整份文档保存——管理员根本改不了认证策略。

漏测原因：`test_auth_policy_settings.py` 直接 import `PlatformSettingsService` 写库（没走真实 `PUT`），而 API 用例只存不含 `auth` 的部分文档——**「整份文档经真实 API 保存」这条路没有任何用例走过**。

### Checklist

- [x] 修 `platform_settings_guard.py` 的判定：改成**分段感知**（只拒「恰好等于」敏感词、或「以 `_password`/`_secret`/`_token`/`_api_key` 结尾」这类形状），**或**改成以 schema 白名单为准、只对白名单之外的未知键做敏感扫描。二者择一，在证据里说明理由
- [x] **结构性回归（关键，防复发）**：新增用例遍历 `default_platform_settings()` 的**全部叶子路径**，断言无一被守卫判定为敏感——只修这一处是治标
- [x] 正向腿仍成立：真敏感形状的键（`auth.password`/`token`/`dsn`/`api_key` 一类）**仍被拒**，且**不回显**触发的键名或值
- [x] 未知键仍被拒（fail-closed）
- [x] [E-22][integration] 经**真实 `PUT /api/v1/platform-settings`** 保存整份默认文档 → 成功并产生新版本；随后真敏感键被拒、未知键被拒。真实边界：**真实 Console API（真实 HTTP）+ 真实 PostgreSQL**
- [x] 先写测试并记录 RED（改前整份文档保存必 400），再修
- [x] 修复后重跑 TASK-010 的 E2E 自检：`npm --prefix apps/console-platform/frontend run build && npm --prefix e2e test -- --config playwright.settings.config.ts`，目标是把 S-01 / E-11 / E-12 从 400 卡点解开（**这是自检，验收仍归需求级 `verify-e2e`**），实跑结果写进证据
- [x] 运行验收命令并填写 Acceptance Evidence

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|--------|---------|--------------------|---------|----------------|---------|------|
| E-22 | integration | 真实 Console API（真实 HTTP PUT）+ 真实 PostgreSQL | 整份默认文档可保存并产生新版本；真敏感键与未知键仍被拒；schema 全部叶子无一被误判 | `tests/console_platform/test_platform_settings_api.py -k business_key_boundary` | ["uv","run","pytest","-q","tests/console_platform/test_platform_settings_api.py","-k","business_key_boundary"] | verified |

### Acceptance Evidence

**RED（改前，`uv run pytest -q tests/console_platform/test_platform_settings_api.py -k business_key_boundary`，2 failed / 1 passed）：**

```
FAILED test_business_key_boundary_full_default_document_saves_via_real_put
  AssertionError: {"code":"PLATFORM_SETTINGS_SECRET_REJECTED","msg":"不允许保存敏感配置项","data":null,...}
  assert 400 == 200
FAILED test_business_key_boundary_default_schema_leaves_are_never_secret
  AssertionError: 合法业务键被误判为敏感：auth.min_password_length
  assert 'auth.min_password_length' is None
```

**修法：分段感知（整键形状），未选 schema 白名单。** 守卫是纯**键名策略**，改用 schema 白名单会让它依赖 `muad_contracts` 的私有结构枚举；且「未知键 fail-closed」已由 `parse_platform_settings` 兜底，白名单方案与既有校验重叠、diff 更大。新口径（`_is_secret_key`）：`_normalize(key)` 后**整键** `== 敏感词` 或 `endswith 敏感词` 才拒，不再子串匹配 ⇒ `min_password_length`(→`minpasswordlength`) 放行；`password`/`token`/`dsn`/`api_key`(→`apikey`)/`db_password`(→`dbpassword`，endswith `password`) 仍拒。

**结构性回归写法**（`test_business_key_boundary_default_schema_leaves_are_never_secret`）：用 `asdict(default_platform_settings())` + `_leaf_paths` 递归展开 schema 全部 **41** 个叶子，逐叶子 `_nest(path, 0)` 还原嵌套后喂 `find_secret_key`，断言均 `None`；再对整份文档断言一次；并钉住 9 个分组与 41 叶子数，schema 变化时强制复核覆盖面。

**GREEN（命令与数字）：**

| 命令 | 结果 |
|---|---|
| `uv run pytest -q tests/console_platform/test_platform_settings_api.py -k business_key_boundary` | **3 passed**（0.38s） |
| `uv run pytest -q tests/console_platform/test_platform_settings_api.py` | **8 passed**（`secret_rejected`/`invalid_payload`/`restore`/`idempotent_replay` 无回归） |
| `uv run pytest -q tests/console_platform` | **176 passed**（认证策略用例经 service 写库，未受影响） |
| `uv run pytest -q tests/test_configuration_inventory.py` | **3 passed**（守卫 4 个常量行号随修改位移，已同步 `docs/configuration-inventory.csv`） |

**断言位置**（`tests/console_platform/test_platform_settings_api.py`）：

- 整份默认文档经真实 PUT 保存并产生新版本：`:365` `test_business_key_boundary_full_default_document_saves_via_real_put` —— `status_code == 200`、`data["revision"] == 1`、`data["settings"]["auth"]["min_password_length"] == 12`、`_revisions(tenant_id) == [1]`、GET `auth.min_password_length`/`auth.session_ttl_hours`。
- 真敏感形状仍拒 + 不回显：`:384` `test_business_key_boundary_rejects_secret_shaped_and_unknown_keys` —— 5 键（`password`/`token`/`dsn`/`api_key`/`db_password`）逐个 400 `PLATFORM_SETTINGS_SECRET_REJECTED`、`secret not in response.text`、`key not in response.text`。
- 未知键 fail-closed：同用例 —— `{"task": {"nope": 1}}` → 400 `VALIDATION_FAILED`，`details[0].path == "task.nope"`，版本名单仍 `[]`。
- 结构性回归：`:340`。

**真实边界**：真实 Console API（`ASGITransport(app=app)` 走真实路由 + 真实 CSRF + 真实封套，真实 `PUT /api/v1/platform-settings`）+ 真实 PostgreSQL（`control.platform_setting` 真行，`_revisions` 直读库）。

**TASK-010 E2E 自检**（`npm --prefix apps/console-platform/frontend run build && npm --prefix e2e test -- --config playwright.settings.config.ts`）：

- 改前：**4 passed / 3 failed**（passed S-03/E-13/E-14/B-05；failed S-01/E-11/E-12，均卡在整份文档 PUT 400）。
- 改后：**7 passed**（10.1s）——S-01 / E-11 / E-12 已从 400 卡点解开。真实边界：真实 Console + 真实 PostgreSQL + 真实构建产物（vite preview）。

> 上述 E2E 为自检；功能验收仍归需求级 `verify-e2e`。
- E-22: verified — automated command passed; run_id=8b27e36d1dff4325b6c5ae658d26d80d (confirmed_by: runner)

### Log
- [2026-10-05] created (draft)
- [2026-10-05] started
- [2026-10-05] completed (done)

---

## TASK-016: 盘点清单分类校准与报告同步

- **Status**: draft
- **Priority**: P1
- **Depends**: TASK-011
- **Source**: platform-settings.backend.design.md#2.3 功能方案, platform-settings.backend.design.md#2.4 范围与边界
- **Spec-Refs**:
- **Acceptance-Refs**: E-16

### Description

TASK-005 为让机检转绿，按 AST 口径**整表重生**了 `docs/configuration-inventory.csv`（约 156 行），并给当时未分类的新符号用了**保守默认**（`python-constant=code`、`settings=environment`）。现在平台设置已接管 41 个叶子，这些行的 `category` 该按设计 §2.3.2 的九个分组逐一校准成 `business`；`docs/configuration-inventory.md` 里指向旧来源的散文也要同步（它在 TASK-006/013 之后已有多处失真）。本任务只做**盘点清单与报告**的口径校准，不再动生产代码。

### Checklist

- [ ] 按设计 §2.3.2 的九个分组逐项校准 `docs/configuration-inventory.csv` 的 `category`：已接入平台设置的标 `business`；仍属部署环境的标 `environment`；协议/枚举/第三方硬上限仍标 `code`
- [ ] 同步 `docs/configuration-inventory.md` 的散文：`:38`/`:46`/`:47`/`:50`/`:55`/`:211` 等仍在讲旧来源（`default_locale`/`artifact_retention_days`/`mcp_max_tools_per_server`/`context_settings_cache_ttl_sec`/`im_progress_interval_sec` 的环境归属）的段落改为事实
- [ ] 复核报告里「必须收敛的重复/冲突」清单：逐条标注现状（已收敛 / 未收敛 / 明确不做），**不得留过期结论**
- [ ] [E-16][integration] 机检：`docs/configuration-inventory.csv` 与当前源码声明逐行一致，被删除/迁移的常量不再出现；分类期望与迁移结论一致。真实边界：**真实源码树 + 真实 CSV + 真实机检（无服务）**
- [ ] 先跑一次记录 RED/基线，再校准
- [ ] 运行验收命令并填写 Acceptance Evidence

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|--------|---------|--------------------|---------|----------------|---------|------|
| E-16 | integration | 真实源码树 + 真实 CSV + 真实机检（无服务） | 清单与源码声明逐行一致；分类期望与迁移结论一致；重复源清单无过期结论 | `tests/test_configuration_inventory.py` | ["uv","run","pytest","-q","tests/test_configuration_inventory.py"] | planned |

### Acceptance Evidence

> 编码期填写 RED/GREEN 与断言位置。

### Log
- [2026-10-05] created (draft)

---

## TASK-017: 滑动续期阈值口径（修 TASK-009 语义变更打破的验收）

- **Status**: done
- **Priority**: P0
- **Depends**: TASK-009, TASK-011
- **Source**: platform-settings.backend.design.md#3.1 方案选型
- **Spec-Refs**:
- **Acceptance-Refs**: E-23

### Description

TASK-009 把滑动续期阈值从模块常量 `SLIDE_THRESHOLD = SESSION_TTL/2` 改成**会话自身窗口的一半**（ADR-12：会话解析不读设置，用 `expires_at - issued_at` 判中点）。对真实会话（窗口 = 12h）这与旧的 6h 规则**完全等价**。

但它打破了已归档需求 console-auth 的两条验收（`test_s02_sliding_renewal_extends_expiry_when_under_half`、`test_b02_six_hour_boundary_renews_only_below`）：那条夹具用 `issued_at=now, expires_at=now+remaining` 造会话 ⇒ **窗口跨度就等于剩余** ⇒ 阈值恒为剩余的一半 ⇒ 永不过中点 ⇒ 断言「剩余 <6h 应续期」必挂。

这是**夹具口径问题，不是实现错误**：把实现退回常量会让每个请求都要读平台设置，违背 ADR-12 与 NFR-PERF-01。正确做法是让夹具按**真实会话的形状**造数据——窗口跨度恒为平台 TTL，靠同时回推 `issued_at` 来调节剩余。

### Checklist

- [x] 改 `tests/acceptance/console_auth_flow/test_auth_acceptance.py` 的 `_issue_session`：让窗口跨度恒为**签发时的平台 TTL**（`issued_at = now - (ttl - remaining)`，`expires_at = now + remaining`），从而在保持真实形状的前提下调节剩余
- [x] **保留 S-02 / B-02 的断言原样**——这是口径更新，不是放宽期望；任何断言的松动都要在证据里单独说明理由
- [x] 全文件跑绿：`uv run pytest -q tests/acceptance/console_auth_flow/test_auth_acceptance.py`（改前 2 failed / 21 passed）
- [x] 复核全仓还有没有别处依赖旧的常量阈值语义（`grep -rn "SLIDE_THRESHOLD\|issued_ttl" apps tests`），有就一并报告
- [x] [E-23][integration] 覆盖：窗口过了中点 ⇒ 续期到 `now + 当前平台 TTL`；未过中点 ⇒ 不续期仅更新 `last_seen_at`；改平台 TTL 不追改已签发会话到期时间。真实边界：**真实 PostgreSQL + 真实登录会话与 CSRF**
- [x] 先跑一次记录 RED，再改夹具
- [x] 运行验收命令并填写 Acceptance Evidence

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|--------|---------|--------------------|---------|----------------|---------|------|
| E-23 | integration | 真实 PostgreSQL + 真实登录会话与 CSRF | 过中点续期到 `now+当前 TTL`；未过中点不续期仅更新活跃时间；不追改已签发会话 | `tests/acceptance/console_auth_flow/test_auth_acceptance.py` | ["uv","run","pytest","-q","tests/acceptance/console_auth_flow/test_auth_acceptance.py"] | verified |

### Acceptance Evidence

**结论：夹具口径修正，实现与断言均未改。** 这是「按真实会话形状造数据」，不是放宽期望——`test_s02` / `test_b02` 的每一行 `assert` 与它们的期望值一个字都没动（下方 diff 只命中 `issued_at=` 一行与新增注释）。

#### RED（改前实测，`uv run pytest -q tests/acceptance/console_auth_flow/test_auth_acceptance.py`）

```
2 failed, 21 passed in 12.13s
FAILED tests/acceptance/console_auth_flow/test_auth_acceptance.py::test_s02_sliding_renewal_extends_expiry_when_under_half
FAILED tests/acceptance/console_auth_flow/test_auth_acceptance.py::test_b02_six_hour_boundary_renews_only_below
```

失败原文（两处均为「应续期却未续期」，`expires_at` 前后相等）：

- `:345` `assert after.expires_at > before.expires_at, "剩余 <6h 必须续期"`
  `AssertionError: 剩余 <6h 必须续期` / `assert 2026-10-05 12:34:50.989608+00:00 > 2026-10-05 12:34:50.989608+00:00`
- `:393` `assert after_below.expires_at > before_below.expires_at, "剩余 <6h 应续期"`
  `AssertionError: 剩余 <6h 应续期` / `assert 2026-10-05 15:34:46.214609+00:00 > 2026-10-05 15:34:46.214609+00:00`

根因：旧夹具 `issued_at=now, expires_at=now+remaining` ⇒ 窗口跨度 = 剩余 ⇒ 阈值 `(expires_at-issued_at)/2` 恒为剩余的一半 ⇒ 永不过中点（实现按 ADR-12 用派生阈值，未改）。

#### GREEN（改后实测）

| 命令 | 结果 |
|------|------|
| `uv run pytest -q tests/acceptance/console_auth_flow/test_auth_acceptance.py`（E-23 契约命令） | **23 passed** in 11.18s |
| `uv run pytest -q tests/console_platform/test_auth_policy_settings.py`（E-15，TASK-009 回归） | **5 passed** |
| `uv run pytest -q tests/acceptance/console_auth_flow/`（整目录，含被改的 environment 种子） | **33 passed** in 12.57s |
| `uv run pytest -q tests/console_auth/test_session.py tests/console_platform/` | **185 passed** |

#### 夹具改动的具体形式

1. `tests/acceptance/console_auth_flow/test_auth_acceptance.py:298` `_issue_session`：窗口跨度改由签发时 TTL 决定——`issued_at = now - (ttl - remaining)`、`expires_at = now + remaining`，新增 `ttl: timedelta = SESSION_TTL`（默认 12h = 平台默认 `auth.session_ttl_hours`；本栈未种 `control.platform_setting`，`read_current` 回落 schema 默认）。使「剩余低于窗口一半」真正等价于「过了窗口中点」，且续期目标仍是 `now + 12h`。
2. `tests/acceptance/console_auth_flow/environment.py:54`（同文件外唯一按同形造会话处）：种子 `builder_token`（`SESSION_REMAINING=3h`，docstring 原写「剩余 <6h → `/auth/me` 应续期」）与 `edge_token`（恰 6h）同样把 `issued_at` 回推 `SESSION_TTL - remaining`。该种子不承载续期断言（`builder_token` 仅用于 `test_environment.py:73` 的「可认证」取证、`edge_token` 无消费方），修正后恢复其文档声称的「临界会话」形状；整目录 33 passed 证明无回归。

**断言改动：没有。** 两个用例的断言文本、期望值、边界取样常量（`BOUNDARY_ABOVE/BELOW = 6h±5s`）全部保持原样。

#### E-23 覆盖点

- 过中点 ⇒ 续期到 `now + 当前平台 TTL`：`test_s02`（剩余 3h，窗口 12h，阈值 6h）断言 `11h < 续期后剩余 <= 12h`；`test_b02` 下侧（6h-5s）断言 `expires_at` 增大。
- 未过中点 ⇒ 不续期、仅更新 `last_seen_at`：`test_b02` 上侧（6h+5s）断言 `expires_at` 不变且 `last_seen_at >= before`。
- 改平台 TTL 不追改已签发会话：由 E-15 `tests/console_platform/test_auth_policy_settings.py::test_session_slide_uses_current_ttl_with_derived_threshold` 覆盖（其夹具本就按 48h/30h 窗口造数，未受影响）。
- 真实边界：E-23 契约命令跑的是真实验收栈——独立 `uvicorn` Console 子进程 + 真实 PostgreSQL（`muad_acc_<uuid>` 空库，`alembic upgrade head` 后 seed）+ 真实登录会话与 CSRF；未 Mock 会话解析链。

#### 旧常量阈值语义的依赖复核

`grep -rn "SLIDE_THRESHOLD\|issued_ttl\|expires_at - issued_at" apps tests` 仅命中：

- `apps/.../application/auth_service.py:127,130,131`——实现本身的派生阈值（ADR-12，本次**未改**）；
- `tests/console_platform/test_auth_policy_settings.py:11,140`——E-15 的 docstring/断言，用 `expires_at - issued_at` 表达「Cookie `Max-Age` 与签发窗口同源」，是派生阈值的**正确**用法。

全仓已无 `SLIDE_THRESHOLD` 符号，无其它依赖旧常量语义处。

**未动 `apps/**` 生产代码；未对任何 dev 库做 DROP/DOWNGRADE（验收栈自建临时库）。**
- E-23: verified — automated command passed; run_id=b4aed9ead9c04227bb92045a62ffba1a (confirmed_by: runner)

### Log
- [2026-10-05] created (draft)
- [2026-10-05] started
- [2026-10-05] completed (done)
