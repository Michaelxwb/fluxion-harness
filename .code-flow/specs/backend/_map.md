# Backend Retrieval Map

> AI 导航地图：定位后端代码结构和关键模块。2026-09-10 由 cf-learn 按真实结构校准（模板假设的 src/api、src/services、src/models 不存在）。

## Purpose

Python 3.12 后端：薄 `apps` 入口 + 胖 `framework` 能力包 + `adapters` 持久化。PostgreSQL 为 SoT，Alembic 单链迁移。

## Architecture

- Framework: FastAPI（`framework/web/create_app` 统一装配：RequestContextMiddleware + AppError→failure 映射）
- ORM/Driver: SQLAlchemy 2 Async + asyncpg
- Database: PostgreSQL（`isf`），三公共字段 + 软删 + partial unique
- Config: `framework/settings.py`（pydantic-settings + `.env`）
- Errors: `AppError(code, message, status_code)`，错误码 `RESOURCE_ACTION` UPPER_SNAKE
- Logging: JSON + `request_id` ContextVar 全链
- Response: `ApiResponse{code,message,data,request_id,timestamp}`，`ok()/failure()`

## Key Files

| File | Purpose |
|------|---------|
| `apps/platform_api/main.py` | API 入口：create_app + `/api/v1` 路由挂载 |
| `apps/platform_api/routes/` | 路由：只转发 Repository + DTO 转换，不写 ORM |
| `apps/platform_api/dependencies.py` | `get_session_factory()`（lru_cache） |
| `framework/web/app.py` | 应用装配：中间件 + 4 个 exception_handler |
| `framework/web/errors.py` | `AppError/NotFoundError/ConflictError` |
| `framework/web/response.py` | `ApiResponse/ok()/failure()` |
| `framework/domain/publish.py` | LIB-01：`build_service_release`（校验+冻结+content_hash） |
| `framework/agent_core/resolver.py` | LIB-02：`resolve_agent` |
| `framework/execution/snapshot.py` | LIB-03：`build_execution_snapshot` |
| `framework/execution/resource_scope_validator.py` | D01 scope 校验 |
| `framework/settings.py` | 集中配置（BaseSettings） |
| `framework/observability/` | JSON 日志 + ContextVar + 脱敏 |
| `adapters/postgres/models.py` | 全部 ORM 模型（单文件聚合，超 500 行是已知观察项） |
| `adapters/postgres/*_repository.py` | Repository：构造注入 session_factory，双件套事务+软删谓词 |
| `adapters/postgres/session.py` | `create_engine_and_session_factory` |
| `migrations/versions/` | `0001` 全量 → `0002` 去发布化，revision 单链 |
| `tests/unit/` | 纯域逻辑单测（`pytest.raises(AppError)` 断 code） |
| `tests/architecture/` | 静态扫描（源码/元数据/迁移链），无 fixture |
| `tests/integration/` | 真实 PG（`TEST_DATABASE_URL`，连不上 skip） |

## Module Map

```
apps/
├── platform_api/    # main + routes/ + dependencies（薄入口）
├── agent_runtime/   # Runtime 入口
├── worker/          # Worker 入口
└── channel_gateway/ # Fastify 网关（Node，见前端域外说明）
framework/
├── web/             # create_app/errors/response/middleware/pagination
├── domain/          # 领域对象 + publish（LIB-01）
├── agent_core/      # resolver + executor
├── execution/       # snapshot/validator/human/delivery/worker_engine
├── contracts/       # 强类型契约 + 错误码常量（就近定义）
├── capability/ channel/ knowledge/ workspace/ auth/ storage/
├── integration/     # scope registry（只组合不反向改 Core）
├── observability/   # logging/context
└── settings.py
adapters/
└── postgres/        # models + *_repository + session/base/soft_delete
migrations/
└── versions/        # 0001_initial_schema → 0002_agent_direct_effect
tests/
├── unit/            # 同步风格纯逻辑
├── architecture/    # 门禁类静态断言
└── integration/     # 真实 PG，手工 teardown
```

## Data Flow

```
Request → RequestContextMiddleware(request_id) → routes/（转发）
        → framework/（domain/publish/resolver/validator）
        → adapters/postgres/*Repository（session.begin 事务）
        → PostgreSQL → ApiResponse Envelope
发布链：publish() → build_service_release → INSERT release（savepoint 幂等）
        → current pointer switch → AuditLogModel（同事务）
```

## Navigation Guide

- 新增 API → `apps/platform_api/routes/` 加路由 + 经 Repository 调 `framework/` 用例，不写 ORM
- 新增表 → `adapters/postgres/models.py` 加模型（含三公共字段）+ `migrations/versions/` 新 revision + Repository
- 错误处理 → 抛 `AppError(code="RESOURCE_ACTION")`，由 `app.py` 转 Envelope
- 配置项 → `framework/settings.py` 集中加字段，禁直读 `os.environ`
- 审计 → 业务写操作同事务插 `AuditLogModel`，details 禁完整 payload
- 测试 → unit 放纯逻辑，门禁放 architecture，PG 相关放 integration（带 skip + teardown）
