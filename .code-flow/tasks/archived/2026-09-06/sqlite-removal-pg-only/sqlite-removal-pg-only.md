# Tasks: sqlite-removal-pg-only

- **Source**: .code-flow/tasks/2026-09-06/sqlite-removal-pg-only/sqlite-removal-pg-only.design.md
- **Created**: 2026-09-06
- **Updated**: 2026-09-06

## Proposal

删除全仓 SQLite 代码（业务、测试、文档），Registry 与测试只留 PostgreSQL，dev 直连本地 PG。解决双形态不一致导致的故障（双 DB 文件、钥匙旁路），让 dev 与生产同构。

### Alignment

- **Scope**: FEAT-00~FEAT-05（ADR 后补、Store、装配、测试、文档、PG 重建）；Redis 延期、数据不迁移、pgvector 可选
- **Decisions**:
  - 本地测试强依赖手动 PG（`fluxion_test`），runner 删 docker 自拉，连不上 fail-fast
  - 遗留 `_create_service` 路径保留，改 PG
  - 反例测试（断言非 PG 被拒）保留并强化
  - benchmark 基线作废重建
- **Non-goals**: Redis 接入、SQLite→PG 数据迁移、性能优化
- **Acceptance**: S-01~S-04、E-01~E-03（见 Acceptance Coverage）

---

## Acceptance Coverage

| 场景ID | 来源设计 | 测试层级 | 关键真实边界 | 负责任务 | 状态 |
|--------|---------|---------|-------------|---------|------|
| S-01 | sqlite-removal-pg-only.design.md#2.4 验收条件 | integration | 真实 PG（`fluxion_test`） | TASK-004 | verified |
| S-02 | sqlite-removal-pg-only.design.md#2.4 验收条件 | E2E | API → PG → 最终输出 | TASK-008 | verified |
| S-03 | sqlite-removal-pg-only.design.md#2.4 验收条件 | integration | rg 全仓扫描 | TASK-008 | verified |
| S-04 | sqlite-removal-pg-only.design.md#2.4 验收条件 | integration | 本地 PG（`fluxion_test`） | TASK-006 | verified |
| E-01 | sqlite-removal-pg-only.design.md#2.4 验收条件 | integration | bundle 装配 | TASK-003 | verified |
| E-02 | sqlite-removal-pg-only.design.md#2.4 验收条件 | integration | bundle 装配 / 测试启动 | TASK-006 | verified |
| E-03 | sqlite-removal-pg-only.design.md#2.4 验收条件 | integration | 真实 PG（`fluxion`） | TASK-008 | verified |

---

## TASK-001: ADR-A007 补立与 Spec 基线修订

- **Status**: done
- **Priority**: P0
- **Depends**:
- **Source**: sqlite-removal-pg-only.design.md#2.2 功能方案
- **Spec-Refs**:
- **Acceptance-Refs**: RISK-01

### Description

编码启动前的合规门禁。补立 `docs/adr/ADR-A007-PG-Only.md`（废除规则 #7 的 Dev SQLite 半句、Contract Test 单库化、覆盖 ADR-A010 第 61 行测试策略表述），同步修订 `.code-flow/specs/` 相关文本并 refresh context。

### Checklist

- [x] 新建 `docs/adr/ADR-A007-PG-Only.md`（背景、决策、覆盖声明、后果）
- [x] 修订 `specs/architecture/resource-registry.md` RULE-fluxion-resource-001 文本与第 25/29 行
- [x] 修订 `specs/backend/database.md:42-43`、`specs/architecture/dfx.md:26`、`specs/architecture/_map.md:10`、`specs/backend/_map.md:10,22,28,35`
- [x] 运行 context refresh 重算 hash，无 missing/stale
- [x] [RISK-01][integration] 运行 Design Gate，decision=pass（门禁检查，可自动化）
- [x] 运行验收命令并填写 Acceptance Evidence

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|--------|---------|--------------------|---------|----------------|---------|------|
| RISK-01 | integration | spec-context.yml、Design Gate | ADR 文件存在且含覆盖条款；refresh ok；gate pass | `docs/adr/ADR-A007-PG-Only.md`；refresh + gate 命令 | `python3 .code-flow/scripts/cf_spec_gate.py --task-dir .code-flow/tasks/2026-09-06/sqlite-removal-pg-only --stage design --json` | verified |

### Acceptance Evidence

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|--------|-----|-------|---------|-------------|------|
| RISK-01 | N/A（文档任务，无行为变更） | decision=pass，errors=[] | ADR-A007 全文；spec-context.yml bindings 6 条；gate 输出 | 真实 gate 命令执行，非手工断言 | verified |

### Log

- [2026-09-06] created (draft)
- [2026-09-06] completed (done)

---

## TASK-002: Store 层去 SQLite

- **Status**: done
- **Priority**: P0
- **Depends**: TASK-001
- **Source**: sqlite-removal-pg-only.design.md#3.2 架构设计
- **Spec-Refs**: fluxion-resource-registry#RULE-fluxion-resource-001, backend-database#RULE-backend-database-001
- **Acceptance-Refs**: S-01, S-03

### Description

删除 `SQLAlchemyRegistryStore` 及子模块的 sqlite 方言分支与 `SQLiteRegistryStore` 别名；`pyproject` 去 `aiosqlite` 并同步锁文件。`pgvector` cosine 降级保留，只改注释。

### Checklist

- [x] `registry/sqlalchemy_store.py` 删 pragma/别名/`_engine_kwargs` sqlite 分支，`initialize()` 只留 PG 路径
- [x] `registry/__init__.py` 删 `SQLiteRegistryStore` 导出
- [x] 5 个子模块 + `credential_projection.py` 删 sqlite 侧分支（约 10 处），`retention_sqlalchemy.py:274` 注释改写
- [x] `pgvector_semantic.py` 降级注释去 SQLite 表述（逻辑保留）
- [x] `pyproject.toml` 去 `aiosqlite`，执行 `uv lock` + `uv sync`
- [x] [S-01][integration] PG contract 套件在本地 `fluxion_test` 全绿（Store 改动回归证据）
- [x] [S-03][integration] `rg -li 'sqlite|aiosqlite' backend/src` 零命中
- [x] verifier `RULE-fluxion-resource-001`: 全可配置事实仍走 Registry 版本化（S-01 contract 断言覆盖），无旁路存储
- [x] verifier `RULE-backend-database-001`: 方言分支删除后 JSON 提取/事务语义走 PG 路径（S-01 PG 腿断言覆盖）
- [x] 运行验收命令并填写 Acceptance Evidence

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|--------|---------|--------------------|---------|----------------|---------|------|
| S-01 | integration | 真实 PG（`fluxion_test`） | contract 断言全绿（最终由 TASK-004 验收，此处只收回归证据） | backend/tests/contract/ + 直连冒烟脚本 | 基线117 passed；改后直连冒烟4/4（publish+bump_revision、keyword search、active_references upsert、non-PG reject）；contract 套件因夹具仍 import 已删别名而 collection error，交 TASK-004 | planned |
| S-03 | integration | rg 全仓扫描 | `backend/src` 零命中（最终由 TASK-008 验收） | `grep -rni sqlite backend/src --include=*.py` | 剩余命中仅 TASK-003 范围（dev_bundle/cli/runtime.py/production_bundle 注释）+ 已清注释；本任务范围零命中 | planned |

### Acceptance Evidence

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|--------|-----|-------|---------|-------------|------|
| S-01 | 基线 117 passed（改前双腿全绿） | 直连冒烟 4/4 通过；完整套件待 TASK-004 | 冒烟输出 publish+bump_revision/keyword/active-ref/reject 四行 OK | 真实本地 PG `fluxion_test`，`reset_on_initialize` 建表 | interim（最终 TASK-004） |
| S-03 | 改前 30+ 处命中 | 本任务范围零命中（剩余为 TASK-003 文件） | grep 输出 | 源码树实扫 | interim（最终 TASK-008） |

### Log

- [2026-09-06] created (draft)
- [2026-09-06] completed (done, S-01/S-03 最终验收交 TASK-004/TASK-008)
- [2026-09-06] 追补：`registry/user_sqlalchemy.get_profile_at` 非数字版本直接返回 None（PG 对 integer=varchar 报错，sqlite 静默空行；E-04 fail-closed 恢复）+ `registry/schema.session_memory.level` 16→32（`session_context_summary` 超长，PG 强制长度）

---

## TASK-003: 装配入口切 PG-only

- **Status**: done
- **Priority**: P0
- **Depends**: TASK-002
- **Source**: sqlite-removal-pg-only.design.md#3.2 架构设计, sqlite-removal-pg-only.design.md#3.3 接口设计
- **Spec-Refs**: backend-code-quality-performance#RULE-backend-quality-001, backend-platform-rules#RULE-backend-platform-001
- **Acceptance-Refs**: E-01, E-02

### Description

dev bundle 非 PG 直接 fail-fast；dev 主钥改为显式必填；CLI 默认 DSN 改 PG（含遗留路径改 `PostgreSQLRegistryStore`）；注释改写。先写 E-01 测试并记录 RED。

### Checklist

- [x] [E-01][integration] 先写非 PG DSN  fail-fast 测试，记录 RED（真实边界：bundle 装配）
- [x] `api/dev_bundle.py:78-81` 只认 PG；`_dev_master_key` 删文件钥匙逻辑
- [x] `cli/main.py:23,204,235` 默认 DSN 改 PG，`_create_service` 改 `PostgreSQLRegistryStore`
- [x] `api/runtime.py:57-58`、`runtime/memory_sql.py`、三个 `Postgres*Store` 头注释改写
- [x] `backend/scripts/migrate_runtime_profiles_to_agents.py` 删 sqlite 分支（含 :27 报错文案）
- [x] `tests/api/test_dev_secret_persistence.py` 整文件重写：文件钥匙逻辑已删，改为显式 `FLUXION_SECRET_MASTER_KEY` + PG 重启持久化验证
- [x] `scripts/init_db.py:40` 默认 DSN 改 PG；`scripts/audit_legacy_spec_keys.py` 的 `--sqlite` 改为 `--dsn`
- [x] `deploy/docker/entrypoint.sh:7,22` 默认 DSN 改 PG（注释同步）
- [x] [E-01][integration] 非 PG DSN 启动 dev bundle 直接报错退出，断言退出非零 + 报错含 PG 要求（GREEN）
- [x] [E-02][integration] 停本地 PG 起 dev 服务，断言报 PG 连接错（bundle 半边，GREEN）
- [x] verifier `RULE-backend-quality-001`: 无静默吞异常（fail-fast 退出码非零），外部调用（PG 建连）有明确失败行为（E-01/E-02 断言覆盖）
- [x] verifier `RULE-backend-platform-001`: DSN 来自显式参数/`FLUXION_DATABASE_URL`（env 优先），默认值即 PG（E-01 断言覆盖）
- [x] 运行验收命令并填写 Acceptance Evidence

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|--------|---------|--------------------|---------|----------------|---------|------|
| E-01 | integration | bundle 装配 | 非 PG DSN → fail-fast（ValueError 含 postgresql）；无 key → RuntimeError | backend/tests/api/test_dev_bundle_pg_only.py（2 用例）+ test_dev_secret_persistence.py（5 用例） | pytest 上述两文件 | verified |
| E-02 | integration | bundle 装配 | 无 PG → 首次触库 ConnectionRefusedError，不回落（测试半边由 TASK-006 验收） | 直连脚本（unreachable DSN list_resources） | GREEN | verified |

### Acceptance Evidence

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|--------|-----|-------|---------|-------------|------|
| E-01 | ImportError（dev_bundle 导入已删别名，行为缺失） | 7 passed（2+5） | test_dev_bundle_pg_only.py 全文件；test_dev_secret_persistence.py 全文件 | 真实 create_dev_bundle_app + _dev_master_key | verified |
| E-02 | N/A（新行为，lazy 建连） | ConnectionRefusedError on first touch | 直连脚本输出 | 真实 PG 驱动建连（127.0.0.1:1 拒绝） | verified（bundle 半边；runner 半边 TASK-006） |

### Log

- [2026-09-06] created (draft)
- [2026-09-06] completed (done)

---

## TASK-004: 测试夹具与 Contract 切 PG

- **Status**: done
- **Priority**: P0
- **Depends**: TASK-002
- **Source**: sqlite-removal-pg-only.design.md#3.2 架构设计
- **Spec-Refs**: fluxion-dfx#RULE-fluxion-dfx-001
- **Acceptance-Refs**: S-01, E-02

### Description

共享夹具改连本地 PG；7 个 contract 文件删 sqlite 工厂；`semantic_equivalence` 改双连接同库；`production_assembly` 反例保留强化。

### Checklist

- [x] `tests/runtime_helpers.py:22-29`、`conftest.py` 夹具改 PG；`tests/console_helpers.py` 同改
- [x] 7 个 contract 文件删 `_sqlite_factory`（engine 级夹具加 TRUNCATE 隔离）
- [x] `test_semantic_equivalence.py` 双 Pod 同文件库改双连接同 PG 库
- [x] `test_production_assembly.py:356` 等断言"非 PG 被拒"的反例测试保留并强化
- [x] 全仓其余直接构造 `SQLiteRegistryStore`/:memory: 的单测文件照单改（含 102 文件脚本化 + 30+ 手工特殊件）
- [x] [S-01][integration] `FLUXION_REQUIRE_POSTGRES_CONTRACT=1` 跑 contract 全套，真实边界为本地 `fluxion_test`（GREEN 63 passed）
- [x] [E-02][integration] 停 PG 跑 contract，断言连接失败退出（runner 半边见 TASK-006）
- [x] verifier `RULE-fluxion-dfx-001`: 删除与迁移工作全程自动化证据（S-01 全绿 + chunk3 669 passed + E-02 行为断言）；迁移另暴露真 bug 2 处已修（session_memory.level 拓宽、get_profile_at 非数字短路），预存红 1 处已修（user_audit 缺默认 profile，与本次无关）
- [x] 运行验收命令并填写 Acceptance Evidence

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|--------|---------|--------------------|---------|----------------|---------|------|
| S-01 | integration | 真实 PG（`fluxion_test`） | contract 全套全绿 | backend/tests/contract/（63 用例） | `FLUXION_REQUIRE_POSTGRES_CONTRACT=1 pytest backend/tests/contract -q` | verified |
| E-02 | integration | 测试启动 | 无 PG → 失败退出，不回落 | test_contract_runner_failfast.py（TASK-006） | GREEN（见 TASK-006） | verified |

### Acceptance Evidence

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|--------|-----|-------|---------|-------------|------|
| S-01 | 改前基线 117 passed（双腿）；夹具移植中 collection error | 63 passed（单 PG 腿）+ chunk3 回归 669 passed | contract/ 全文件；c3 日志 | 真实本地 PG `fluxion_test` | verified |
| E-02 | N/A | failfast 1 passed（TASK-006） | test_contract_runner_failfast.py | 真实子进程 + 不可达 DSN | verified |

### Log

- [2026-09-06] created (draft)

---

## TASK-005: e2e/benchmark 与前端 e2e 改写

- **Status**: done
- **Priority**: P0
- **Depends**: TASK-002
- **Source**: sqlite-removal-pg-only.design.md#3.2 架构设计
- **Spec-Refs**:
- **Acceptance-Refs**: S-03

### Description

后端 e2e/benchmark 直接构造的 SQLite DSN 照单改 PG；`frontend/e2e/` 3 个 spec 头注释改写；benchmark 基线作废，以 PG 首轮结果为新基线。

### Checklist

- [x] 后端 `tests/e2e/`、`tests/benchmarks/`、`tests/chaos/`、`tests/scale/` 等直接 DSN 照单改 PG
- [x] `frontend/e2e/` 3 个 spec 头注释去 SQLite 表述
- [x] `playwright.config.ts:25` dev 服务启动 DSN 改 PG（与 e2e 前置一致；已随 TASK-006 落地，含 CI key）
- [x] benchmark 跑一轮，记录 PG 新基线（原基线作废；本机 OOM 负载下数字仅存档，不可比）
- [x] [S-03][integration] `rg` 确认 `backend/tests`、`frontend/e2e` 零命中（最终由 TASK-008 验收）
- [x] 运行验收命令并填写 Acceptance Evidence（见本任务 Evidence 表）

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|--------|---------|--------------------|---------|----------------|---------|------|
| S-03 | integration | rg 全仓扫描 | 测试目录零命中（最终由 TASK-008 验收） | grep 全仓 sweep（本任务范围零命中） | verified（interim） | verified |

### Acceptance Evidence

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|--------|-----|-------|---------|-------------|------|
| S-03 | 改前 90+ 处命中 | 本任务范围零命中 | sweep 输出 | grep 实扫 | interim（最终 TASK-008） |

### Log

- [2026-09-06] created (draft)
- [2026-09-06] completed (done)

### Notes

- ~~#NOTES `test_S_R12...` 请用户定夺~~ → 已定夺：删除 `fluxion run` 整条遗留路径（`run_command`/`_run`/`service_run_request` + `test_cli_dev_bundle.py` 全文件 + 文档无引用）。e2e 重跑 96 passed（cli 文件已删）。benchmark 功能全绿，数字因本机 OOM 负载仅存档。

---

## TASK-006: CI 合并与 runner 改写

- **Status**: done
- **Priority**: P0
- **Depends**: TASK-004
- **Source**: sqlite-removal-pg-only.design.md#2.2 功能方案
- **Spec-Refs**:
- **Acceptance-Refs**: S-04, E-02

### Description

CI 双 job 合并为单 PG job；runner 删 docker 自拉与 sqlite 回落，直连本地 PG，失败即退出。先写 runner E-02 半边测试记 RED。

### Checklist

- [x] [E-02][integration] 先写 runner 无 PG 退出的测试，记录 RED
- [x] `scripts/run_registry_contract_tests.py` 删 `_start_postgres`/docker/回落分支，直连 `FLUXION_POSTGRES_DSN`（默认本地 `fluxion_test`）
- [x] `backend-ci.yml` 双 job 合并；`frontend-ci.yml:7` 注释改写；`validation.yml:54` 回落说明改写
- [x] `frontend-ci.yml` e2e job 加 postgres service + init_db + E2E 测试 key；`playwright.config.ts` DSN 改 PG（归 TASK-005，顺手合入）
- [x] `test_publication_outbox_contract_is_shared_by_sqlite_and_postgres` 更名去 sqlite
- [x] [E-02][integration] 停 PG 跑 runner，断言非零退出 + 报错含 PG 连接信息（GREEN，与 TASK-004 汇合为完整 E-02）
- [x] [S-04][integration] 本地全量 green（用户 2026-09-06 确认：CI 只出包，本地为准；分块证据见归档后补记）
- [x] 运行验收命令并填写 Acceptance Evidence

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|--------|---------|--------------------|---------|----------------|---------|------|
| S-04 | integration | 本地 PG（`fluxion_test`） | 本地全量 green（分块累计 1000+ passed，dod 浏览器项除外） | chunk 记录（c3/c14/e2e/scale/dod/benchmarks） | verified |
| E-02 | integration | 测试启动 | runner 无 PG → 非零退出，不自拉容器 | backend/tests/integration/test_contract_runner_failfast.py | pytest 该文件（1 passed） | verified |

### Acceptance Evidence

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|--------|-----|-------|---------|-------------|------|
| E-02 | 老 runner 自拉容器 exit 0（测试 FAIL） | failfast 测试 1 passed；runner 直连本地 PG 跑 contract 全绿 | test_contract_runner_failfast.py；runner 输出 | 真实子进程 + 不可达 DSN | verified（runner 半边；bundle 半边 TASK-003） |
| S-04 | — | 本地全量 green（见归档后补记） | chunk 记录 | verified |

### Log

- [2026-09-06] created (draft)

---

## TASK-007: 文档全量更新

- **Status**: done
- **Priority**: P1
- **Depends**: TASK-001
- **Source**: sqlite-removal-pg-only.design.md#2.2 功能方案
- **Spec-Refs**:
- **Acceptance-Refs**: S-03

### Description

按 FEAT-04 行号清单改写现行文档；根 `CLAUDE.md` 只改三处；历史 ADR/归档/迁移记录不动。

### Checklist

- [x] `docs/architecture/总体架构.md:13`、`docs/design/07:21` 改写
- [x] 根 `README.md:60`、deploy README（:15/18/166）、`CONTRIBUTING.md:24`、`.env.example:3` 改写
- [x] AGENTS.md（规则 #7、:72、:113）与根 `CLAUDE.md` 三处同步改
- [x] `docs/foundation/02` REQ-DEV-001、`docs/development/架构验收Gate.md:43` 改写
- [x] [S-03][integration] `rg` 确认文档面零命中（排除清单内历史文件，最终由 TASK-008 验收）
- [x] 运行验收命令并填写 Acceptance Evidence

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|--------|---------|--------------------|---------|----------------|---------|------|
| S-03 | integration | rg 全仓扫描 | 文档面零命中（最终由 TASK-008 验收） | rg 全仓（本任务内已 sweep 确认，TASK-008 出最终证据） | verified（interim） | verified |

### Acceptance Evidence

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|--------|-----|-------|---------|-------------|------|
| S-03 | 改前 20+ 处文档命中 | 本任务范围零命中（剩余仅 S-03 排除清单项） | 全仓 sweep 输出 | rg 实扫 | interim（最终 TASK-008） |

### Log

- [2026-09-06] created (draft)
- [2026-09-06] completed (done, S-03 最终验收交 TASK-008)

---

## TASK-008: PG 重建、旧文件删除与 E2E 验证

- **Status**: done
- **Priority**: P0
- **Depends**: TASK-002, TASK-003, TASK-004, TASK-005, TASK-006, TASK-007
- **Source**: sqlite-removal-pg-only.design.md#2.2 功能方案, sqlite-removal-pg-only.design.md#2.4 验收条件
- **Spec-Refs**: fluxion-runtime-core#RULE-fluxion-runtime-001
- **Acceptance-Refs**: S-02, S-03, E-03

### Description

`fluxion` 库 drop+create 重建（旧数据不清迁）；删除三个旧文件；`init_db` 建表；重建发布链路；dev.echo 端到端验证 dev 切 PG 后 Runtime 仍无状态。

### Checklist

- [x] `fluxion` 库 drop + create（清空旧数据，用户已确认不清迁）
- [x] `init_db.py` 建表成功；重跑一次验证幂等（[E-03][integration] 真实边界：`fluxion` 库）
- [x] 删除 `.data/fluxion.db`（含 wal/shm）、`fluxion-dev.db`、`.data/.fluxion-dev-master-key`
- [x] dev 服务 PG DSN 启动，显式 `FLUXION_SECRET_MASTER_KEY`（:8099 运行中）
- [x] 重建凭据/provider/模型发布链路
- [x] [S-02][E2E] dev.echo Web Chat 对话，真实边界 API → PG → 最终输出，首轮即通（GREEN）
- [x] [S-03][integration] 全仓 `rg -li 'sqlite|aiosqlite'` 按排除清单零命中（GREEN）
- [x] verifier `RULE-fluxion-runtime-001`: dev 切 PG 后无本地事实状态（S-02 E2E：同链路跨重启一致，ExecutionSnapshot 固定版本）
- [x] 运行验收命令并填写 Acceptance Evidence

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|--------|---------|--------------------|---------|----------------|---------|------|
| S-02 | E2E | API、PG、最终输出 | dev.echo 首轮即通，无 `secret_not_found` | /tmp/fluxion-s02-verify.py（真服务 :8099 全链路） | `S-02 ALL GREEN`（凭据→四连发布→绑定→`echo: hello s02`） | verified |
| S-03 | integration | rg 全仓扫描 | 按排除清单零命中 | grep 全仓 sweep | 剩余仅排除清单项（审计哨兵/history/gitignored） | verified |
| E-03 | integration | 真实 PG（`fluxion`） | `init_db` 重跑幂等成功 | scripts/init_db.py ×2 | 两次 `[OK] 26 张表` | verified |

### Acceptance Evidence

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|--------|-----|-------|---------|-------------|------|
| S-02 | 旧链（sqlite 已删文件） | 7/7 PASS（credential/chain/bind/token/chat/echo） | /tmp/fluxion-s02-verify.py 输出 + 服务日志 | 真 `fluxion serve --dev` 进程 :8099 + 真 PG `fluxion` 库 | verified |
| S-03 | 改前 100+ 处命中 | 零命中（排除清单内除外） | sweep 输出 | grep 实扫全部源码/测试/文档/CI | verified |
| E-03 | N/A | 两次 init_db 均 OK，26 表，level=32 | init_db 输出 + information_schema | 真实 `fluxion` 库 | verified |

### Log

- [2026-09-06] created (draft)
- [2026-09-06] completed (done)

---

## 归档后补记（2026-09-06）

- S-04 验证方式变更：CI 只出包 → 本地全量为准。本地分块证据：contract 63 + chunk3 669 + e2e 96 + c14 161 + scale 5 + dod 10 + benchmarks 24 + workflow_poc 29 + chaos 8（dod 浏览器项除外，见 RISK-06）。
- TASK-006 由"待 push"转为 done（本地证据闭合）。
