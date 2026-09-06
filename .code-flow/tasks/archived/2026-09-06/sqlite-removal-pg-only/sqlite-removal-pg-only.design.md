# SQLite 清除与 PG-Only 重构模块需求与设计简报

> **文档编号**: MOD-SQLITE-REMOVAL-v0.1
> **文档版本**: v0.1
> **创建日期**: 2026-09-06
> **文档状态**: 草稿

**评审边界说明**:
- **需求评审**: 第 2 章（需求分析）→ 通过后锁定需求基线
- **设计评审**: 第 3 章（技术设计）→ 通过后锁定设计基线

**ID 体系**: FEAT（功能）、NFR（非功能指标）
场景编号：S-（正常）、E-（异常）

**适用场景**: 小型重构

---

## 目录

- [1. 文档控制](#1-文档控制)
- [2. 需求分析](#2-需求分析)
  - [2.1 需求概述](#21-需求概述)
  - [2.2 功能方案](#22-功能方案)
  - [2.3 范围与边界](#23-范围与边界)
  - [2.4 验收条件](#24-验收条件)
- [3. 技术设计](#3-技术设计)
  - [3.1 技术选型](#31-技术选型)
  - [3.2 架构设计](#32-架构设计)
  - [3.3 接口设计](#33-接口设计)
  - [3.4 性能与容量考量](#34-性能与容量考量)
- [4. 风险与依赖](#4-风险与依赖)
- [Spec Compliance Matrix](#spec-compliance-matrix)
- [附录：术语表](#附录术语表)

---

## 1. 文档控制

### 1.1 责任人

| 角色 | 姓名 | 职责范围 |
|------|------|---------|
| 开发负责人 | 待定 | 技术方案、代码实现 |
| 测试负责人 | 待定 | 测试策略、质量保证 |

### 1.2 修订历史

| 版本 | 日期 | 作者 | 变更描述 |
|------|------|------|---------|
| v0.1 | 2026-09-06 | cf-task-align | 初始草稿（对齐结论：全量删 SQLite、Redis 暂缓、数据不迁移） |

---

## 2. 需求分析

### 2.1 需求概述

| 项目 | 内容 |
|------|------|
| **模块名称** | sqlite-removal-pg-only |
| **需求类型** | 重构 |
| **业务背景** | Dev SQLite / Prod PostgreSQL 双形态导致 dev 与生产行为不一致（如 Secret 双 DB 文件并存、钥匙文件旁路、方言分支维护成本）。本地已有 PG（mmuser/mmuser@localhost:5432）与 Redis，要求 dev 直接模拟生产架构。 |
| **核心目标** | 全仓删除 SQLite 相关代码（业务代码、单测、E2E、文档），只保留 PostgreSQL；dev 直连本地 PG。 |

---

### 2.2 功能方案

#### 2.2.1 功能清单

| 功能ID | 功能名称 | 功能描述 | 优先级 | 来源 |
|--------|---------|---------|--------|------|
| FEAT-00 | ADR-A007 后补 | 在编码启动前补立 ADR-A007，正式修订架构规则 #7（废除 Dev SQLite）与 Contract Test 单库化；本设计简报先行声明修订内容 | P0 | 需求描述 |
| FEAT-01 | Store 层去 SQLite | `SQLAlchemyRegistryStore` 及 5 个子模块删除方言 sqlite 分支与 `SQLiteRegistryStore` 别名；`pyproject` 去 `aiosqlite` 后执行 `uv lock` + `uv sync`（`uv.lock`/venv 同步）；`redis` 依赖保留（`services/cache.py:12` 顶层强依赖，代码本次不动） | P0 | 需求描述 |
| FEAT-02 | 装配入口切 PG-only | dev bundle 非 PG DSN 直接 fail-fast；dev 主钥改为必须显式 `FLUXION_SECRET_MASTER_KEY`；CLI/脚本/部署默认 DSN 全改 PG | P0 | 需求描述 |
| FEAT-03 | 测试切 PG | 共享夹具改连本地 PG（`fluxion_test`）；contract 测试删 sqlite 工厂只留 PG；e2e/benchmark 照单改；`frontend/e2e/` 3 个 spec 头注释改写；CI 改写（`backend-ci.yml` 双 job 合并、`frontend-ci.yml:7` 注释、`validation.yml:54` 回落说明）；`scripts/run_registry_contract_tests.py` 删除 docker 自拉逻辑，默认直连本地 PG（`FLUXION_POSTGRES_DSN` 可覆盖，默认 `postgresql+asyncpg://mmuser:mmuser@localhost:5432/fluxion_test`），连不上直接 fail-fast 退出（用户 2026-09-06 确认：不自动拉容器，强依赖手动 PG） | P0 | 需求描述 |
| FEAT-04 | 文档与 Spec 全量更新 | `docs/architecture/总体架构.md:13`、`docs/design/07:21`、根 `README.md:60`、deploy README、`CONTRIBUTING.md:24`、`.env.example`、AGENTS.md 全文相关行（规则 #7、第 72/113 行）、根 `CLAUDE.md` 仅同步改 SQLite 三处（全文已与 AGENTS.md 漂移，不做全文同步）、`.code-flow/specs/`（含 RULE-fluxion-resource-001 文本修订）、`docs/foundation/02-核心需求.md` REQ-DEV-001、`docs/development/架构验收Gate.md` 中 SQLite/dev 开箱描述全部改写；现行 ADR-A010 第 61 行测试策略表述由 ADR-A007 覆盖声明（A010 正文不动）；历史 ADR（ADR-A001~A013）与已归档任务文档不动 | P1 | 需求描述 |
| FEAT-05 | PG 重建与旧文件删除 | `fluxion` 库 drop+create 重建（数据不清迁、旧数据全部清除）；删除 `.data/fluxion.db`（含 wal/shm）、`fluxion-dev.db`、`.fluxion-dev-master-key` | P0 | 需求描述 |

#### 2.2.2 字段约束

不涉及数据存储变更（`registry/schema.py` 表结构原样，同一套 DDL 在 PG 上重建）。跳过。

---

### 2.3 范围与边界

| 类别 | 内容 |
|------|------|
| **范围（In Scope）** | FEAT-00~FEAT-05；`init_db.py` 在 PG 空库重建；重建后 dev 凭据/provider/模型链路重新发布验证（S-02） |
| **非范围（Out of Scope）** | Redis 接入（明确延期）；SQLite→PG 数据迁移（不做，空库重建）；pgvector 安装（可选，缺失时自动降级 Python cosine）；性能优化 |
| **有意妥协 / 技术债** | ① ADR-A007 后补：与架构规则 #25"先 ADR 再改 Contract"顺序冲突，用户已确认接受，编码启动前必须补立（见 RISK-01）。② 本地跑测试强依赖 PG 常驻，无 PG 则全红（用户已确认接受；pytest 为串行执行，无 xdist 并行冲突）。③ benchmark 基线作废重建：`:memory:`→PG 后数字不可比，原基线作废，以 PG 首轮结果为新基线。 |

---

### 2.4 验收条件

#### 2.4.1 功能验收场景

**正常场景**

| 场景ID | 功能ID | 优先级 | 测试层级 | 关键真实边界 | 操作步骤 | 预期结果 |
|--------|--------|--------|---------|-------------|---------|---------|
| S-01 | FEAT-01/03 | P0 | integration | 真实 PG（`fluxion_test`） | `FLUXION_REQUIRE_POSTGRES_CONTRACT=1` 跑 contract 全套 | 全绿 |
| S-02 | FEAT-02/05 | P0 | E2E | API → PG → 最终输出 | PG 空库 `init_db` → dev 服务启动 → 重建凭据/provider/发布 → Web Chat 对话（可用 dev.echo 模型，无需外部 key） | 首轮即通，无 `secret_not_found` |
| S-03 | FEAT-01/03/04 | P0 | integration | rg 全仓扫描 | `rg -li 'sqlite\|aiosqlite'`（排除对象仅限：历史 ADR `docs/adr/ADR-A*` 正文、已归档任务 `.code-flow/tasks/archived/`、`docs/foundation/01` 中性举例、`docs/migration/` 历史偏差记录、`.code-flow/config.yml` 工具 tags、审计规则 `scripts/local_state_audit.py` R2 及其测试探针（防回潮哨兵，保留）；现行 ADR-A010 由 ADR-A007 覆盖声明，正文不动） | 零命中 |
| S-04 | FEAT-03 | P1 | integration | 本地 PG（`fluxion_test`） | 本地全量 `pytest backend/tests` 通过（CI 只出包不验证，本地为准；dod 浏览器项除外，见 RISK-06） | 全绿 |

**异常场景**

| 场景ID | 功能ID | 测试层级 | 关键真实边界 | 触发条件 | 系统行为 |
|--------|--------|---------|-------------|---------|---------|
| E-01 | FEAT-02 | integration | bundle 装配 | dev bundle 传入非 PG DSN | fail-fast 报错，不回落 SQLite |
| E-02 | FEAT-02/03 | integration | bundle 装配 / 测试启动 | 本地 PG 未启动时起 dev 服务/跑测试（含 contract runner） | 报 PG 连接错并 fail-fast，不自拉容器、不回落 |
| E-03 | FEAT-05 | integration | 真实 PG（`fluxion`） | `init_db.py` 在已有表库上重跑 | 幂等成功（`checkfirst` 只建缺失） |

#### 2.4.2 非功能指标

无新增性能指标（无性能敏感点）。环境前置约束：本地 PG 常驻（5432，`fluxion`/`fluxion_test` 库已建）、`FLUXION_SECRET_MASTER_KEY` 显式设置。

---

## 3. 技术设计

### 3.1 技术选型

| 类别 | 选型 | 版本 | 选型理由 |
|------|------|------|---------|
| 语言 | Python | 3.12+ | 沿用既有栈 |
| 数据库 | PostgreSQL + asyncpg | PG16 / asyncpg 0.30+ | 唯一事实源；与生产同形态 |
| ORM | SQLAlchemy Core（Table + 手写 SQL） | 2.0+ | 沿用既有实现，只删方言分支 |
| 测试 | pytest + 本地 PG | 沿用 | contract 工厂已有 `reset_on_initialize` PG 模式 |
| 移除 | aiosqlite / SQLite | — | 本次删除对象 |

---

### 3.2 架构设计

```mermaid
graph LR
    CLI["fluxion serve --dev"] --> DEV["create_dev_bundle_app"]
    DEV --> STORE["PostgreSQLRegistryStore"]
    STORE --> PG[("PostgreSQL fluxion")]
    DEV -.->|非PG DSN| FAIL["fail-fast"]
```

| 层级 | 改动 |
|------|------|
| Store | `registry/sqlalchemy_store.py:66-100,981-986` 删 pragma/别名/`_engine_kwargs` sqlite 分支，`initialize()` 只留 PG 路径；`registry/__init__.py:1,35` 删 `SQLiteRegistryStore` 导出；`resource/publish/workflow_run/channel/retention_sqlalchemy.py` 与 `repositories/credential_projection.py` 删 sqlite 侧分支（约 10 处，`retention_sqlalchemy.py:274` 注释同步改写）；`pgvector_semantic.py` 的 Python cosine 降级保留（pgvector 缺失时仍需），注释去掉 SQLite 表述 |
| 装配 | `api/dev_bundle.py:78-81` 只认 PG；`_dev_master_key:182-227` 删文件钥匙逻辑，dev 也要求显式 key；`api/runtime.py:57-58` aiosqlite 注释改写；`runtime/memory_sql.py` 与 `repositories/trace_store.py/approval_store.py/eval_run_store.py` 头注释"SQLite 契约"改写；`cli/main.py:23,204,235` 默认 DSN 改 PG，其中无 flag 遗留路径 `_create_service:204` 保留、硬编码 `SQLiteRegistryStore` 改为 `PostgreSQLRegistryStore`（用户 2026-09-06 确认）；`backend/scripts/migrate_runtime_profiles_to_agents.py:23-24` 删 sqlite 分支；`services/cache.py`（`TenantRedisCache`）本次不动（Redis 延期） |
| 脚本部署 | `scripts/init_db.py:40` 默认 DSN 改 PG；`scripts/audit_legacy_spec_keys.py` 的 `--sqlite` 改为 `--dsn`（PG）；`deploy/docker/entrypoint.sh:22`、`.env.example:3`、`playwright.config.ts:25` 同改；`pyproject.toml:15` 去 `aiosqlite` |
| 测试 | `tests/runtime_helpers.py:22-29`、`conftest.py` 夹具改 PG；`tests/console_helpers.py` 同改；7 个 contract 文件删 `_sqlite_factory`；`test_semantic_equivalence.py` 双 Pod 同文件库改双连接同 PG 库；`test_production_assembly.py:356` 等断言"非 PG 被拒"的反例测试保留并强化；其余 e2e/benchmark 照单改 DSN；CI 合并 job（CI 内 PG 由 Actions service 提供，非 docker 自拉）；`scripts/run_registry_contract_tests.py` 删 docker 自拉与 sqlite 回落，直连本地 PG、失败即退出 |
| 文档与 Spec | FEAT-04 清单；`.code-flow/specs/` 修订后重算 hash 并 refresh context（见 RISK-05） |

`services`→`domain contracts`→`repositories` 依赖方向不变；服务进程不建表（`init_db` 唯一入口）规则不变。

---

### 3.3 接口设计

#### 形态 B：CLI 命令

| 命令 | 参数 / Flag | 说明 | 退出码 |
|------|------------|------|--------|
| `fluxion serve --dev` | `--registry-dsn`（默认 `postgresql+asyncpg://mmuser:mmuser@localhost:5432/fluxion`） | dev 服务，DSN 非 PG 前缀直接报错退出 | 0=成功 / 非 0=失败 |
| `python3 scripts/init_db.py` | `--dsn`（默认同上） | PG 建表（`checkfirst` 幂等）；要求库已存在 | 0=成功 / 非 0=失败 |

环境变量：`FLUXION_DATABASE_URL`（必填 PG DSN，无默认值回落）、`FLUXION_SECRET_MASTER_KEY`（dev/prod 均必填，base64 32B）。stdout/stderr 分工与现有 CLI 一致，不新增机器可读输出。

---

### 3.4 性能与容量考量

无性能敏感点（纯删除重构，无新增写路径/高频调用/大数据量变更）。跳过。

---

## 4. 风险与依赖

### 4.1 项目依赖

| 依赖模块 | 依赖内容 | 风险等级 |
|---------|---------|---------|
| 本地 PG | 5432 常驻，`fluxion`/`fluxion_test` 库可用 | 中（已确认存在） |
| CI | postgres service 可用，单 job 化改造 | 低 |

### 4.2 风险识别

| 风险ID | 描述 | 影响 | 应对措施 | 验证场景 |
|--------|------|------|---------|---------|
| RISK-01 | ADR-A007 后补，与规则 #25 顺序冲突 | 架构合规性 | 编码启动前必须先立 ADR，作为 plan 阶段门禁 | S-01（门禁检查，无法自动化则人工确认） |
| RISK-02 | 无 PG 时本地测试/服务全红 | 本地开发体验 | README 前置要求 + fail-fast 明确报错 | E-02 |
| RISK-03 | 本地 PG 无 pgvector | 语义检索降级 cosine，结果排序一致性不变 | 不阻塞；deploy 文档标注可选安装 | S-02（含一次 recall 验证） |
| RISK-04 | PG 重建后旧凭据链丢失，dev 对话不可用 | 联调中断 | FEAT-05 后按 S-02 重建发布链路 | S-02 |
| RISK-05 | `.code-flow/specs/` 修订后 hash 漂移，Gate 失败 | 流程阻塞 | plan 阶段执行 context refresh 重算 hash（cf-sync 规则） | Design Gate pass |
| RISK-06 | 真浏览器 E2E（playwright/dod-09）本地无浏览器跑不了，CI 只出包不验证 | 前端 E2E 首验 deferred | playwright.config + 前端 CI job 文件已改对（含 PG/key/init_db）；有浏览器环境后首跑即验 | manual（无可跑环境，原因见此行） |

---

## Spec Compliance Matrix

| Spec/Rule | enforcement | 设计影响 | 设计落点 | 验证场景 | 状态/N/A 理由 |
|-----------|-------------|---------|---------|---------|----------------|
| `fluxion-resource-registry`#RULE-fluxion-resource-001 | required | "SQLite 必须实现相同 Contract"条款被本次修订（ADR-A007 后补） | §2.2 FEAT-00 / §2.3 技术债① | S-01 + S-03 | applied |
| `backend-database`#RULE-backend-database-001 | required | 删方言分支须遵守 schema/数据访问约束 | §3.2 Store 行 | S-01 | applied |
| `backend-code-quality-performance`#RULE-backend-quality-001 | required | fail-fast 不静默吞异常；删除工作全量测试覆盖 | §3.2 装配行 / §2.4 | E-01 + E-02 + S-01 | applied |
| `fluxion-dfx`#RULE-fluxion-dfx-001 | required | 删除与重建必须有自动化证据 | §2.4 全部场景 | S-01 ~ S-04 | applied |
| `backend-platform-rules`#RULE-backend-platform-001 | required | DSN/env 变更走 env 优先；dev 也要求显式 key | §3.3 | E-01 | applied |
| `fluxion-runtime-core`#RULE-fluxion-runtime-001 | required | dev 切 PG 后 Runtime 仍无状态，不引入本地事实 | §3.2 架构设计 | S-02 | applied |

---

## 附录：术语表

| 术语 | 定义 |
|------|------|
| SoT | Source of Truth，事实源 |
| fail-fast | 启动期即报错退出，不带病运行 |
| Contract Test | 双库（现单库）同语义契约测试 |

---

*文档结束*

---

## 附录：归档后补记（2026-09-06）

用户确认 CI 只出包不验证，验证以本地为准，故：
- S-04 由"推送后 CI 单 PG job"改为"本地全量 `pytest backend/tests` 通过"；
- 新增 RISK-06（真浏览器 E2E 本地/CI 均不可跑，deferred）。
