# Plan: core-domain-publish（核心领域与发布模型 E2E 缺口闭合）

> 设计输入：`core-domain-publish.design.md` v1.2（Design Gate: pass）  
> Spec Context：5 bindings（database / quality-performance / directory-structure / logging / platform-rules），code-stage pending  
> 基线事实：LIB-01/02/03 库层已实现（unit+smoke）；本 plan 只做**缺口闭合**，不重写已实现逻辑。每个任务开工前先查现有测试覆盖，缺啥补啥，禁止重复造用例。

## 任务清单

> 状态（2026-09-10）：T1–T7 全部完成；pytest 44 passed；ruff check + format（本次触碰文件）干净；
> mypy 本次文件无新增错误（models.py 21 处 bare-dict 为历史遗留，未动）；
> Design Gate pass，Code Gate pass。

### T1 — E-04 非法 scope 拒绝 ✅
- 内容：`resource_scope_validator` 未知 type / `schema_hash` 与 Release 冻结值不一致 → `SERVICE_CONFIGURATION_INVALID`；S-05 合法路径有缺就补。
- 落点：`framework/execution/resource_scope_validator.py`，用例进 `tests/unit/test_publishing.py` 或新建 `tests/unit/test_scope_validation.py`。
- 验收：E-04（integration，真实 validator + 真实 Release 行，不 mock 校验核心）；S-05 合法创建成功。
- Spec：code-quality-performance（显式错误对象）、platform-rules（错误码 taxonomy）。

### T2 — S-06 binding 删除回归门禁 ✅
- 内容：architecture test 断言：alembic head 含 `0002_agent_direct_effect`；全库 grep `user_agent_binding` 零命中（models / repositories / routes / frontend）；路由解析只走 default_agent + Routing Policy。
- 落点：`tests/architecture/` 新用例。
- 验收：S-06。
- Spec：database（迁移链）、directory-structure（分层落点）。

### T3 — E-02 published 不可变守卫 ✅
- 内容：`ServiceRepository` 不暴露 published 快照 update 方法；直调底层 update 路径抛错；单测覆盖。
- 落点：`adapters/postgres/service_repository.py` + unit 用例。
- 验收：E-02（Repository → Published Snapshot 真实边界，不 mock Repository）。
- Spec：database（三公共字段/软删不受影响，update 守卫不绕过 `is_deleted` 过滤）。

### T4 — S-02 发布原子事务 ✅
- 内容：`publish_service` validate + snapshot persist + current pointer switch 同一事务；注入失败（validator 抛错 / DB 约束冲突）后断言无半发布（无孤儿 release 行、current_release_id 未动）。
- 落点：`framework/domain/publish.py` + `adapters/postgres/service_repository.py`，integration 用例跑真实 PG（本地 `isf` 库）。
- 验收：S-02。
- Spec：database（事务边界）、logging（audit event 含 release_id/content_hash）。

### T5 — S-01/S-03/S-04 联调补齐 ✅
- 内容：S-01 通用对象持久化共用；S-03 Execution 后撤销权限→按 Snapshot 跑业务逻辑、按当前状态重校验权限；S-04 双 checksum artifact 切换，旧不可变。
- 落点：integration 用例（真实 Repository + 真实 PG）。
- 验收：S-01、S-03、S-04。
- Spec：database、code-quality-performance。

### T6 — 可观测与 Envelope ✅（另含实现：`publish()` 同事务写 `service.published` audit event + `request_id` 参数）
- 内容：发布/停用/禁用 audit event 断言（entity_id/release_id/revision/content_hash，有 request_id/execution_id，无完整敏感 payload）；Envelope `ok()` 结构 + `AppError` 管线单测（`test_response.py` 查漏补缺）。
- 验收：NFR-OBS-01；S-02 audit 断言；E-04 错误码可识别。
- Spec：logging、platform-rules。

### T7 — 全量回归与回填 ✅
- 内容：`.venv/bin/python -m pytest -q` 全绿；`ruff` + `mypy` 过门禁；`cf_spec_context.py refresh` 无 drift；按实际落点给 5 条 code-stage rule 做 applications 回填，跑 code Gate。
- 验收：26+新增用例全过；code Gate pass（或显式列出 block 项）。

## 执行顺序

```
T2 ─┬─▶ T1 ─▶ T4 ─▶ T5 ─▶ T6 ─▶ T7
    └─▶ T3 ─┘
```

- T2/T3 无依赖，可并行先行（纯加固，不碰业务逻辑）。
- T1→T4：validator 行为锁定后才能测发布事务失败路径。
- T7 最后收口。

## 不做事项（Out of Scope，重申）

- 不新增 LIB 接口（FEAT-03 保持内部契约）；不加 DB trigger；§4.3 其余阈值不等实测不编数字；不碰 02 模块 HTTP 路由。
