# cf-task:align 最终校验报告

> **日期**：2026-09-10  
> **框架版本**：v0.3.0  
> **总体设计**：V1.6

## 1. 代码库扫描

已检查：

```text
.code-flow/specs/framework/_map.md
pyproject.toml
frontend/console/package.json
apps/channel_gateway/package.json
apps/**
framework/**
adapters/**
integrations/**
migrations/**
tests/**
docs/**
```

## 2. 模板覆盖

| 模板 | 文档数 | 覆盖范围 |
|---|---:|---|
| design-full | 13 | Core、Control、Agent、Execution、Worker、Capability、Knowledge、Auth、Channel、Workspace、Memory、Integration |
| design-lite | 2 | 存储/基础设施、可观测/Web 公共基础 |
| design-frontend | 1 | Console 前端 |

另外 `.code-flow/tasks/2026-09-10/framework-v0.3.0/` 保存 backend/full、foundation/lite、frontend 三类 cf-task:align 设计产物。

## 3. 数据库公共字段

SQLAlchemy metadata 当前 **31 张 Framework 自建表**，统一包含：

```text
is_deleted  BOOLEAN NOT NULL DEFAULT FALSE
create_time TIMESTAMPTZ NOT NULL DEFAULT now()
update_time TIMESTAMPTZ NOT NULL DEFAULT now()
```

同时检查：

```text
字段非空
时间字段带 timezone
update_time ORM onupdate
初始 Alembic Migration 31/31 包含公共字段
Repository soft-delete predicate
避免机械建立 is_deleted 单列低选择性索引
```

第三方自行管理的 LangGraph Checkpointer Schema 不属于 Framework 自建表，不修改其内部 Schema。

## 4. 当前自动化结果

```text
pytest: 22 passed
python compileall: passed
```

覆盖：

```text
Core Purity
DB Common Fields
Initial Migration Common Fields
Soft Delete Query
Sandbox Boundary
Response Envelope
Sensitive Log Redaction
Semi Design / StandardListPage
Full/Lite/Frontend 设计模板章节完整性
cf-task:align 三类设计产物模板完整性
```

## 5. 真实环境 Gate

以下不能以当前本地 mock/static test 代替，进入生产实现时仍必须执行：

```text
真实 PostgreSQL migration/transaction
Worker SIGKILL reclaim
Multi-Pod Agent Runtime
Redis Down
Control Plane Down
真实 Channel Delivery
Production isolated Sandbox
Console Browser E2E
```

原 scaffold 未包含 code-flow 官方 `cf_spec_context.py/cf_spec_gate.py`，因此本报告不伪造官方 `Design Gate=pass`。required rules 已写入 spec-context/Spec Compliance Matrix，并使用仓库 Architecture Tests 验证当前可自动化部分。
