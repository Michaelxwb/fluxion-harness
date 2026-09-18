---
id: backend-database
description: 涉及数据库/ORM/迁移/查询时适用：schema 与数据访问约束
stages: [design, plan, code, review]
enforcement: required
verifiers:
  - rule: RULE-backend-database-001
    type: manual
    config:
      checklist: Confirm all Guidance and Avoid items for this Spec.
      owner: project-owner
---

# Backend Database

## Examples

✅ 参数化查询，明确列出字段

```python
cur.execute("SELECT id, name FROM users WHERE email = %s", (email,))
```

❌ 字符串拼接用户输入（SQL 注入）+ `SELECT *`

```python
cur.execute(f"SELECT * FROM users WHERE email = '{email}'")
```

## Rules
- [RULE-backend-database-001] The implementation must satisfy every applicable item in Guidance and avoid every item in Avoid.

## Guidance
- 所有 SQL 必须参数化，禁止字符串拼接 / 模板插值用户输入
- [docs/数据库设计基线 + `adapters/postgres`] 自建表必含 `is_deleted / create_time / update_time`；Repository 默认过滤 `is_deleted=false`，删除默认软删；可重建资源用 `partial unique WHERE is_deleted=false`
- [docs] 破坏性 schema 变更走 Alembic `expand → migrate → switch → contract`，禁止滚动发布中新旧 Pod 因 schema 不兼容同时失败；未上线/无线上数据时允许直接破坏性迁移（例 `0002` 直接 `DROP COLUMN`，downgrade 用 `IF NOT EXISTS` 补回）
- 迁移脚本形态：`UPGRADE_STATEMENTS: list[str]` 原始 SQL + `op.execute` 循环，`revision/down_revision` 单链
- 迁移脚本必须可回滚，或写成幂等脚本（`IF NOT EXISTS` / `ON CONFLICT`）
- 事务边界明确：跨表写入必须在同一事务内，禁止"半提交"状态
- 涉及索引/锁的 schema 变更必须评估线上影响，大表慎用 `ALTER TABLE` 阻塞操作
- 每个 model 必须有对应 Repository 抽象（`adapters/postgres/<model>_repository.py`，构造注入 `async_sessionmaker`），路由/handler 只做转发+ DTO 转换，禁止在 service / handler 直接写 ORM 查询或裸 SQL

## Patterns
- Repository 双件套：`async with factory() as session: async with session.begin():` 包住读写；一切查询带 `.where(Model.is_deleted.is_(False))` 软删谓词
- 读写分离场景显式标注读库/写库，强一致读走主库
- N+1 查询用预加载（`joinedload` / `include` / `Preload`）解决
- 大批量写入分批 + commit，避免单事务过大
- 缓存与数据库一致性：先写库再失效缓存（`cache-aside`）
- CRUD 基类统一实现 `get / list / create / update / delete / bulk_*`，子类只扩展模型特有查询

## Avoid
- 禁止在事务内发起外部 HTTP / RPC 调用，超时会导致连接池耗尽
- 禁止在循环中执行单条 `INSERT` / `UPDATE`，必须批量化
- 禁止在 ORM 之外手写 SQL 时绕过参数绑定
- 禁止用 `SELECT *` 上线，明确列出字段控制传输与索引
