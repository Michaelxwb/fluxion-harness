# ADR-A007 PG-Only：废除 Dev SQLite，Registry 与测试只留 PostgreSQL

**引用**：规则 7（Dev SQLite / Prod PostgreSQL）、规则 25（Contract 变更须经 ADR）、REQ-DEV-001、ADR-A010 第 61 行。

**背景**：

Dev SQLite / Prod PostgreSQL 双形态导致 dev 与生产行为不一致，2026-09-06 实测故障：

- Secret 双 DB 文件并存（`.data/fluxion.db` 与 `fluxion-dev.db`），`secret_credentials` 只存在其一，运行时按 DSN 连错库即 `secret_not_found`；
- dev 主钥旁路（DB 旁 `.fluxion-dev-master-key` 文件钥匙），与生产显式 key 语义不一致；
- 方言分支（sqlite upsert/pragma/`json_extract`）与 PG 路径长期双维护；
- 本地已有 PG（`mmuser/mmuser@localhost:5432`），dev 可直连，无需零依赖开箱。

**候选**：

1. 维持双形态，仅文档强调 DSN 一致性；
2. PG-Only：全仓删除 SQLite（业务代码、单测、E2E、现行文档），dev 直连本地 PG，测试强依赖手动 PG；
3. PG-Only 但测试保留 `:memory:` 快测。

**决策**：选 2（用户 2026-09-06 确认全量删除；选 1 留隐患，选 3 留双维护成本）。

细则：

- **Store 层**：`SQLAlchemyRegistryStore` 删 sqlite 分支与 `SQLiteRegistryStore` 别名；`pyproject` 去 `aiosqlite`；`pgvector` 缺失时的 Python cosine 降级保留（与 SQLite 无关）；
- **装配**：dev bundle 非 PG DSN 直接 fail-fast；dev 主钥改为显式 `FLUXION_SECRET_MASTER_KEY` 必填；CLI（含无 flag 遗留路径）默认 DSN 改 PG；
- **测试**：夹具改连本地 `fluxion_test`；contract 删 sqlite 工厂；runner 删 docker 自拉与 sqlite 回落，直连本地 PG，失败即退出；CI 双 job 合并（CI 内 PG 由 Actions service 提供）；
- **数据**：不清迁，`fluxion` 库 drop+create 重建；删除 `.data/fluxion.db`（含 wal/shm）、`fluxion-dev.db`、`.data/.fluxion-dev-master-key`；
- **Redis 延期**：`TenantRedisCache`/Streams 代码与 `redis` 依赖保留，本次不动。

**覆盖声明**：

- 规则 7 修订为「Dev/Prod 同 PostgreSQL，共享同一 RegistryStore 实现与同一套 Contract Test（单库）」；
- REQ-DEV-001 修订为 PG dev/prod 共享领域模型与 Store Contract；
- ADR-A010 第 61 行「SQLite/PG 同 Contract 测试覆盖」表述由本 ADR 覆盖，A010 正文不动。

**代价**：

- 本地跑测试/服务强依赖 PG 常驻，无 PG 则全红；
- benchmark 基线作废重建；
- 重建后 dev 发布链路（凭据/provider/模型）需重新发布验证。

**失败模式**：

- 非 PG DSN → fail-fast，错误明确要求 PG；
- PG 未启动 → 报连接错，不自拉容器、不回落。

**验收**：

- contract 全套在本地 `fluxion_test` 全绿；
- dev.echo Web Chat 端到端首轮即通；
- `rg -li 'sqlite|aiosqlite'` 按排除清单（历史 ADR、归档任务、迁移记录、中性举例、工具 tags）零命中。
