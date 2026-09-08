# Tasks: 终版整改（105-commits 问题清单收敛）

- **Source**: `.code-flow/tasks/2026-09-08/final-remediation/final-remediation.backend.design.md`, `.code-flow/tasks/2026-09-08/final-remediation/final-remediation.frontend.design.md`
- **Created**: 2026-09-08
- **Updated**: 2026-09-08

## Proposal

落实 105-commits 终版整改 7 项：Compose 补齐三角色应用拓扑（PG/Redis 全外部化）、RuntimeProfile V2 删除幽灵字段、Hook loader 接线＋8 固定点＋删死抽象、L1 缓存 TTL=0 双 bypass、resolver 接入 scoped read、trace 四态落盘＋Console 展示。无兼容设计，DB 删除重建。做完后底层架构进入稳定阶段。

## Acceptance Coverage

| 场景ID | 来源设计 | 测试层级 | 关键真实边界 | 负责任务 | 状态 |
|---|---|---|---|---|---|
| S-01 | final-remediation.backend.design.md#2.5 验收条件 | manual | docker 引擎＋用户自备外部 PG | TASK-001 | planned |
| S-02 | final-remediation.backend.design.md#2.5 验收条件 | integration | 真实 Console 服务 → PG | TASK-003 | planned |
| S-03 | final-remediation.backend.design.md#2.5 验收条件 | integration | 真实 Console 服务 → PG → 真实执行 | TASK-003 | planned |
| E-01 | final-remediation.backend.design.md#2.5 验收条件 | unit | 纯契约层（model＋validation） | TASK-002 | verified |
| S-04 | final-remediation.backend.design.md#2.5 验收条件 | integration | 真实 service＋entry_points fixture 包 | TASK-006 | planned |
| E-02 | final-remediation.backend.design.md#2.5 验收条件 | integration | 真实 service＋故障 hook | TASK-006 | planned |
| S-05 | final-remediation.backend.design.md#2.5 验收条件 | unit | 纯契约层（enum＋dispatch） | TASK-007 | planned |
| S-06 | final-remediation.backend.design.md#2.5 验收条件 | unit | 真实 resolver＋PG，大量不同 user | TASK-008 | planned |
| S-07 | final-remediation.backend.design.md#2.5 验收条件 | integration | 真实 PG＋并发 publish | TASK-009 | planned |
| B-01 | final-remediation.backend.design.md#2.5 验收条件 | integration | 真实 PG 事务超时路径 | TASK-009 | planned |
| S-08 | final-remediation.backend.design.md#2.5 验收条件 | integration | 真实 service＋PG，四种终态执行 | TASK-010 | planned |
| S-F1 | final-remediation.frontend.design.md#2.4 验收条件 | unit | vitest 组件＋mirror | TASK-011 | planned |
| S-F2 | final-remediation.frontend.design.md#2.4 验收条件 | unit | vitest 组件＋创建请求体 | TASK-011 | planned |
| E-F1 | final-remediation.frontend.design.md#2.4 验收条件 | integration | 真实 Console 服务＋PG（422 通道） | TASK-011 | planned |
| S-F3 | final-remediation.frontend.design.md#2.4 验收条件 | E2E | 真浏览器＋dev 后端＋PG | TASK-012 | planned |

---

## TASK-001: Compose 三角色拓扑＋PG/Redis 外部化

- **Status**: in-progress
- **Priority**: P0
- **Depends**:
- **Source**: final-remediation.backend.design.md#3.2 架构设计
- **Spec-Refs**: backend-platform-rules#RULE-backend-platform-001
- **Acceptance-Refs**: S-01, RULE-backend-platform-001

### Description

`deploy/docker/docker-compose.yml` 只保留应用角色：api/runtime/worker 同镜像（`FLUXION_ROLE` 区分），删 `postgres` 服务＋数据卷；`FLUXION_DATABASE_URL`/`FLUXION_REDIS_URL` 必填外部传入（缺失 fail-fast）；`deploy/README.md` 同步。本任务即视为 TASK-001（runtime-execution-remediation）挂起解除后的承接。

### Checklist
- [ ] [S-01][manual] 按 README 前置准备外部 PG，`docker compose config` 校验通过；`up --scale runtime=3` 三实例就绪；kill 一个 runtime 后请求仍成功且 trace 完整；确认无 postgres/redis 服务残留（manual：需 docker 守护进程＋用户自备外部 PG，无法自动化）
- [ ] verifier `RULE-backend-platform-001`：以 S-01 手册证据验证应用角色编排＋外部依赖 env 化
- [ ] 运行验收命令并填写 Acceptance Evidence

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| S-01 | manual | docker 引擎、用户自备外部 PG | 三角色就绪；scale=3；kill 不丢事实；无内置 PG/Redis | ——（manual） | `docker compose -f deploy/docker/docker-compose.yml config`＋手册 checklist | planned |
| RULE-backend-platform-001 | manual | 同上 | 应用角色编排＋外部依赖 env 化，由 S-01 提供证据 | —— | 同上 | planned |

### Acceptance Evidence

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|--------|-----|-------|---------|-------------|------|
| S-01 | ——（文件级：旧 compose 含 postgres 服务即 RED 状态） | config 校验通过（部分）：三角色＋无 PG/Redis 服务＋DSN 必填外部传入 | docker-compose.yml＋README | `docker compose config`✅；`up --scale/kill` 实机待用户执行（manual） | pending |

### Log
- [2026-09-08] created (draft)
- [2026-09-08] in-progress：compose 文件＋README 改完＋config 校验通过；live-fire（up/scale/kill）待用户实机执行

---

## TASK-002: RuntimeProfile V2 模型删字段

- **Status**: done
- **Priority**: P1
- **Depends**:
- **Source**: final-remediation.backend.design.md#3.3 数据设计
- **Spec-Refs**: fluxion-resource-registry#RULE-fluxion-resource-001
- **Acceptance-Refs**: S-02, E-01, RULE-fluxion-resource-001

### Description

`RuntimeProfile` 删除 `request_timeout_ms`/`max_retries`/`concurrency`/`memory_budget_mb`/`schema_version`，仅保留 `max_rounds`/`default`/`bootstrapped_from`；删除 v1 兼容入口（`read_published_profile`、`PROFILE_SCHEMA_VERSIONS`）；`validate_profile_write` 收敛为严格校验主入口。无兼容、无迁移（DB 删除重建）。

### Checklist
- [x] [E-01][unit] 先写测试并记录 RED：v1 兼容入口 import 即失败；未知历史版本概念不存在（RED：6 failed，实现前；GREEN：8 passed）
- [x] [S-02][integration] 含已删字段的 spec 写校验失败（422 级语义，字段定位），与 TASK-003 的端到端共用断言口径
- [x] verifier `RULE-fluxion-resource-001`：以 S-02 行为证据验证 PG 唯一实现、版本化与 tenant 隔离
- [x] 运行验收命令并填写 Acceptance Evidence（contract 112＋e2e 106＋integration 348＋console 156 全绿）

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| S-02 | integration | 真实 Console 服务 → PG | 含已删字段即拒，定位字段；由 TASK-003 最终验收，本任务以后端契约层为据 | backend/tests/contract/test_runtime_profile_versions.py（改写为 V2） | `.venv/bin/python -m pytest -q backend/tests/contract/test_runtime_profile_versions.py` | verified |
| E-01 | unit | 纯契约层 | v1 兼容入口不存在；未知版本无语义 | 同上 | 同上 | verified |
| RULE-fluxion-resource-001 | integration | 真实 PG 单库＋tenant scope | PG 唯一实现、版本化、tenant 隔离，由 S-02 提供行为证据 | 同上 | 同上 | verified |

### Acceptance Evidence

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|--------|-----|-------|---------|-------------|------|
| E-01 | 6 failed（字段仍在、兼容入口仍在） | 8 passed；ruff+mypy clean（UP037 既有） | test_runtime_profile_versions.py | 纯契约层；`hasattr` 双断言＋字段集精确断言 | verified |
| S-02 | 同上（模型层） | 同上＋compat/producers 14 passed | 同上 | 5 字段逐一被拒＋定位；合法 v2 通过 | verified |

### Log
- [2026-09-08] created (draft)
- [2026-09-08] completed (done)：V2 模型＋入口前向同步＋全仓 seed/断言同步，E-01/S-02 verified

---

## TASK-003: V2 生产入口同步

- **Status**: draft
- **Priority**: P1
- **Depends**: TASK-002
- **Source**: final-remediation.backend.design.md#2.3 功能方案
- **Spec-Refs**: 无新增唯一 Rule owner；继承 Context 与依赖任务约束
- **Acceptance-Refs**: S-02, S-03

### Description

`CreateRuntimeProfileRequest` 去 4 字段；`_runtime_profile_spec`、`default_runtime_profile_request`、`_ensure_platform_default_profile` 同步收敛（显式 v2 形状）；provider 级同名字段（`model_providers.py`、provider 表单）保留不动并在注释隔离。

### Checklist
- [ ] [S-02][integration] 含已删字段的创建请求被拒（S-02 最终验收负责人）
- [ ] [S-03][integration] 合法 v2 建→发布→执行，`max_rounds` 生效（S-03 最终验收负责人）
- [ ] 运行验收命令并填写 Acceptance Evidence

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| S-02 | integration | 真实 Console 服务 → PG | 同上（本任务为最终验收负责人） | backend/tests/integration/test_runtime_profile_schema_compat.py（改写为 V2） | `.venv/bin/python -m pytest -q backend/tests/integration/test_runtime_profile_schema_compat.py` | planned |
| S-03 | integration | 真实 Console 服务 → PG → 真实执行 | v2 发布＋执行行为一致 | backend/tests/integration/test_runtime_profile_producers.py（改写为 V2） | `.venv/bin/python -m pytest -q backend/tests/integration/test_runtime_profile_producers.py` | planned |

### Acceptance Evidence

> `cf-task-start` 在编码期填写 RED/GREEN 结果、每个关键断言的位置和真实组件证据；全部状态 verified 后任务才可 done。

### Log
- [2026-09-08] created (draft)

---

## TASK-004: V2 测试与 seed 断言同步

- **Status**: draft
- **Priority**: P1
- **Depends**: TASK-002, TASK-003
- **Source**: final-remediation.backend.design.md#3.5 质量实现方案
- **Spec-Refs**: 无新增唯一 Rule owner；继承 Context 与依赖任务约束
- **Acceptance-Refs**: S-02, S-03

### Description

全仓测试 seed/fixture/断言集同步 V2 形状：`_MECHANICS_FIELDS`、`RS6`、architecture profile 断言、`console_helpers.runtime_profile_spec`、e2e seed（含 `agent-editor-lifecycle`、`chat-nfr`）、`capacity_verify` 构造点。不改生产语义，只同步测试数据。

### Checklist
- [ ] [S-02/S-03][integration] 全回归绿：contract＋services＋integration＋architecture 相关文件
- [ ] 运行验收命令并填写 Acceptance Evidence

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| S-02 | integration | 同 TASK-003（本任务为协同方，非最终负责人） | seed 全为合法 v2 | backend/tests/architecture/test_runtime_profile_architecture.py 等 | `.venv/bin/python -m pytest -q backend/tests/contract backend/tests/architecture` | planned |
| S-03 | integration | 同 TASK-003（协同方） | 同上 | backend/tests/e2e/test_profile_execution_effect.py 等 | `.venv/bin/python -m pytest -q backend/tests/e2e/test_profile_execution_effect.py backend/tests/e2e/test_profile_rollback_compat.py` | planned |

### Acceptance Evidence

> `cf-task-start` 在编码期填写 RED/GREEN 结果、每个关键断言的位置和真实组件证据；全部状态 verified 后任务才可 done。

### Log
- [2026-09-08] created (draft)

---

## TASK-005: Hook loader 接线＋entry_points 发现

- **Status**: draft
- **Priority**: P1
- **Depends**:
- **Source**: final-remediation.backend.design.md#3.4 接口设计
- **Spec-Refs**: 无新增唯一 Rule owner；继承 Context 与依赖任务约束
- **Acceptance-Refs**: S-04, E-02

### Description

`PluginLoader` 接受 `HookRegistryProtocol` 并分派 HOOK 分支（删早退）；新增 `discover_hooks()` 经 `importlib.metadata.entry_points(group="fluxion.hooks")` 解析（格式错误 fail-fast）；composition root（dev bundle/production/serve 装配点）启动加载；无 entry_points 即空集。

### Checklist
- [ ] [S-04][integration] fixture 包提供 entry_points hook → service 执行前后有 audit 记录；卸载（空集）后行为不变
- [ ] [E-02][integration] 故障 hook 按 fail_policy 收敛（与 TASK-006 共用口径）
- [ ] 运行验收命令并填写 Acceptance Evidence

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| S-04 | integration | 真实 service＋entry_points fixture 包 | 安装/卸载行为一致；由 TASK-006 最终验收，本任务以 loader 分派为据 | backend/tests/unit/test_hook_loader.py（新增） | `.venv/bin/python -m pytest -q backend/tests/unit/test_hook_loader.py` | planned |
| E-02 | integration | 真实 service＋故障 hook | 同上（协同方） | 同上 | 同上 | planned |

### Acceptance Evidence

> `cf-task-start` 在编码期填写 RED/GREEN 结果、每个关键断言的位置和真实组件证据；全部状态 verified 后任务才可 done。

### Log
- [2026-09-08] created (draft)

---

## TASK-006: 7 个固定 Hook Point＋AuditHook 示例

- **Status**: draft
- **Priority**: P1
- **Depends**: TASK-005
- **Source**: final-remediation.backend.design.md#3.4 接口设计
- **Spec-Refs**: fluxion-runtime-core#RULE-fluxion-runtime-001, backend-logging#RULE-backend-logging-001
- **Acceptance-Refs**: S-04, E-02, RULE-fluxion-runtime-001, RULE-backend-logging-001

### Description

`kernel/events.py` 新增 7 个 frozen 只读 payload（Before/AfterExecution、Before/AfterModelCall、AfterToolCall、OnExecutionError/Cancelled）；`run()/stream()/run_step/_execute_model_tool`/取消错误分支埋分发点（复用 `before_tool_call` 三件套：frozen＋拷贝＋返回值丢弃，授权先于 hook）；`examples/audit_hook/`（或 docs 示例）提供 AuditHookPlugin。

### Checklist
- [ ] [S-04][integration] S-04 最终验收负责人：8 点位各至少一次触发断言＋audit 记录
- [ ] [E-02][integration] FAIL_CLOSED 阻断业务；FAIL_OPEN 业务继续＋异常记录；日志带 ID 脱敏
- [ ] verifier `RULE-fluxion-runtime-001`：以 S-04 行为证据验证无状态＋kernel 只依赖 Contract
- [ ] verifier `RULE-backend-logging-001`：以 E-02 行为证据验证关联 ID＋脱敏
- [ ] 运行验收命令并填写 Acceptance Evidence

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| S-04 | integration | 真实 service＋entry_points fixture 包 | 8 点位触发＋audit 落点 | backend/tests/integration/test_hooks.py（扩展） | `.venv/bin/python -m pytest -q backend/tests/integration/test_hooks.py` | planned |
| E-02 | integration | 真实 service＋故障 hook | fail 策略收敛＋日志证据 | 同上 | 同上 | planned |
| RULE-fluxion-runtime-001 | integration | 真实 service | 无状态＋kernel 只依赖 Contract，由 S-04 提供行为证据 | 同上 | 同上 | planned |
| RULE-fluxion-logging-001 | integration | 真实 service | 关联 ID＋脱敏，由 E-02 提供行为证据 | 同上 | 同上 | planned |

### Acceptance Evidence

> `cf-task-start` 在编码期填写 RED/GREEN 结果、每个关键断言的位置和真实组件证据；全部状态 verified 后任务才可 done。

### Log
- [2026-09-08] created (draft)

---

## TASK-007: 删除 HookScope/scope_id/IGNORE

- **Status**: draft
- **Priority**: P2
- **Depends**:
- **Source**: final-remediation.backend.design.md#2.3 功能方案
- **Spec-Refs**: 无新增唯一 Rule owner；继承 Context 与依赖任务约束
- **Acceptance-Refs**: S-05

### Description

删 `HookScope` 枚举、`HookRegistration.scope/scope_id`、`FailPolicy.IGNORE`（dispatch 中与 FAIL_OPEN 同分支合并）；约 10 个测试文件的 `scope=HookScope.GLOBAL` 参数机械删除；`P1ViewPage` 占位文案保留（非菜单，不动）。

### Checklist
- [ ] [S-05][unit] 先写测试并记录 RED：`HookScope`/`IGNORE` import 即失败；既有 hook 30+ 用例全绿（仅删参数，行为不变）
- [ ] 运行验收命令并填写 Acceptance Evidence

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| S-05 | unit | 纯契约层 | 死抽象不存在；行为无变化 | backend/tests/unit/test_hook_scheduler.py 等 | `.venv/bin/python -m pytest -q backend/tests/unit/test_hook_scheduler.py backend/tests/integration/test_hooks.py` | planned |

### Acceptance Evidence

> `cf-task-start` 在编码期填写 RED/GREEN 结果、每个关键断言的位置和真实组件证据；全部状态 verified 后任务才可 done。

### Log
- [2026-09-08] created (draft)

---

## TASK-008: L1 Cache TTL≤0 双 bypass

- **Status**: draft
- **Priority**: P1
- **Depends**:
- **Source**: final-remediation.backend.design.md#3.5 质量实现方案
- **Spec-Refs**: fluxion-dfx#RULE-fluxion-dfx-001
- **Acceptance-Refs**: S-06, RULE-fluxion-dfx-001

### Description

`resolve()` 入口 TTL≤0 时跳过 L1 读＋跳过写（bypass，不删代码，未来启用留后路）；B-ID-02 用例保留显式开 TTL 演练分支。

### Checklist
- [ ] [S-06][unit] 先写测试并记录 RED：1000 个不同 user resolve 后 `_l1_cache == {}`；内存稳定不断言绝对值，只断言零增长
- [ ] resolver benchmark 不劣化（NFR-01 对比基线）
- [ ] verifier `RULE-fluxion-dfx-001`：以 S-06＋benchmark 证据验证有界内存与性能基线
- [ ] 运行验收命令并填写 Acceptance Evidence

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| S-06 | unit | 真实 resolver＋PG，大量不同 user | 零增长；benchmark 不劣化 | backend/tests/services/test_context_resolver.py（扩展） | `.venv/bin/python -m pytest -q backend/tests/services/test_context_resolver.py` | planned |
| RULE-fluxion-dfx-001 | unit | 同上 | 有界内存＋性能基线，由 S-06 提供行为证据 | 同上 | 同上 | planned |

### Acceptance Evidence

> `cf-task-start` 在编码期填写 RED/GREEN 结果、每个关键断言的位置和真实组件证据；全部状态 verified 后任务才可 done。

### Log
- [2026-09-08] created (draft)

---

## TASK-009: resolver 接入 scoped read

- **Status**: draft
- **Priority**: P2
- **Depends**:
- **Source**: final-remediation.backend.design.md#3.2 架构设计
- **Spec-Refs**: backend-database#RULE-backend-database-001, backend-code-quality-performance#RULE-backend-quality-001
- **Acceptance-Refs**: S-07, B-01, RULE-backend-database-001, RULE-backend-quality-001

### Description

`resolve()` 内"读 revision→读 6 类配置→组 snapshot"包进 `store.begin_scoped_read(tenant)`；Credential/Memory I/O 保持在 scope 之外（逐点核对）；`begin_scoped_read` 纳入 store Protocol（`ChannelRegistryStore` 或其父二选一，测试 fakes 同步）；有界失败沿用类型化错误。

### Checklist
- [ ] [S-07][integration] 先写测试并记录 RED：resolve 中途并发 publish → 快照全旧版（S-07 最终验收负责人）
- [ ] [B-01][integration] 超时类型化、无无穷等待
- [ ] verifier `RULE-backend-database-001`：以 S-07 行为证据验证只读一致事务、参数化查询与 tenant
- [ ] verifier `RULE-backend-quality-001`：以 B-01 行为证据验证有界外部调用与异常保留
- [ ] 运行验收命令并填写 Acceptance Evidence

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| S-07 | integration | 真实 PG＋并发 publish | 全旧或全新，无混合 | backend/tests/integration/test_registry_consistent_resolution.py（扩展 resolver 级） | `.venv/bin/python -m pytest -q backend/tests/integration/test_registry_consistent_resolution.py` | planned |
| B-01 | integration | 真实 PG 事务超时路径 | 类型化超时 | 同上 | 同上 | planned |
| RULE-backend-database-001 | integration | 真实 PG | 只读一致事务＋参数化＋tenant，由 S-07 提供行为证据 | 同上 | 同上 | planned |
| RULE-backend-quality-001 | integration | 真实 PG | 有界外部调用＋异常保留，由 B-01 提供行为证据 | 同上 | 同上 | planned |

### Acceptance Evidence

> `cf-task-start` 在编码期填写 RED/GREEN 结果、每个关键断言的位置和真实组件证据；全部状态 verified 后任务才可 done。

### Log
- [2026-09-08] created (draft)

---

## TASK-010: trace status 四态落盘

- **Status**: draft
- **Priority**: P2
- **Depends**:
- **Source**: final-remediation.backend.design.md#3.3 数据设计
- **Spec-Refs**: 无新增唯一 Rule owner；继承 Context 与依赖任务约束
- **Acceptance-Refs**: S-08

### Description

`TraceRecord` 加 `status`（复用 `ExecutionTerminalState` 四值）；`_append_trace` 加参，`run()/stream()` 取 `session.finalize()` 返回值传入；`trace_records` 加 nullable `status` 列（`init_db.py` 唯一事实源，DB 重建无迁移）。

### Checklist
- [ ] [S-08][integration] 先写测试并记录 RED：四种终态执行 → trace status 一一对应（S-08 最终验收负责人）
- [ ] 运行验收命令并填写 Acceptance Evidence

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| S-08 | integration | 真实 service＋PG，四种终态执行 | status 一一对应 | backend/tests/integration/test_runtime_terminal_paths.py（扩展）或新增 trace status 用例 | `.venv/bin/python -m pytest -q backend/tests/integration/test_runtime_terminal_paths.py` | planned |

### Acceptance Evidence

> `cf-task-start` 在编码期填写 RED/GREEN 结果、每个关键断言的位置和真实组件证据；全部状态 verified 后任务才可 done。

### Log
- [2026-09-08] created (draft)

---

## TASK-011: Console 删除 V2 字段

- **Status**: draft
- **Priority**: P1
- **Depends**: TASK-002
- **Source**: final-remediation.frontend.design.md#2.2 功能方案
- **Spec-Refs**: frontend-quality-standards#RULE-frontend-quality-001, frontend-component-specs#RULE-frontend-component-001
- **Acceptance-Refs**: S-F1, S-F2, E-F1, RULE-frontend-quality-001, RULE-frontend-component-001

### Description

`inMemorySchemas.ts` runtime_profile 镜像删 4 字段（含兼容文案）；`AgentEditorForm` 默认创建 spec 去两字段、删兼容说明；`SchemaForm.test.tsx` 断言同步；provider 级同名字段保留。

### Checklist
- [ ] [S-F1][unit] mirror 表单无 4 字段输入；缺省不含它们
- [ ] [S-F2][unit] 默认创建请求体最小集
- [ ] [E-F1][integration] 含已删字段 draft → 422＋字段级错误经 PublishIssues 通道可见（后端已覆盖，本任务以前端回归为据）
- [ ] verifier `RULE-frontend-quality-001`：以 S-F1 行为证据验证禁 any 与三态
- [ ] verifier `RULE-frontend-component-001`：以 S-F2 行为证据验证受控表单与 services 分层
- [ ] 运行 `pnpm -r typecheck`、lint、约束脚本并填写 Acceptance Evidence

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| S-F1 | unit | vitest 组件＋mirror | 无字段＋无缺省 | frontend/apps/console/src/components/__tests__/SchemaForm.test.tsx | `pnpm --filter @fluxion/console test` | planned |
| S-F2 | unit | vitest 组件＋创建请求体 | 最小集 | 同上 | 同上 | planned |
| E-F1 | integration | 真实 Console 服务＋PG | 422＋字段错误可见 | 同上（回归）＋后端 S-02 | 同上 | planned |
| RULE-frontend-quality-001 | unit | 同上 | 禁 any＋三态，由 S-F1 提供行为证据 | 同上 | 同上 | planned |
| RULE-frontend-component-001 | unit | 同上 | 受控表单＋services，由 S-F2 提供行为证据 | 同上 | 同上 | planned |

### Acceptance Evidence

> `cf-task-start` 在编码期填写 RED/GREEN 结果、每个关键断言的位置和真实组件证据；全部状态 verified 后任务才可 done。

### Log
- [2026-09-08] created (draft)

---

## TASK-012: Console status 列＋e2e seed 同步

- **Status**: draft
- **Priority**: P1
- **Depends**: TASK-010, TASK-011
- **Source**: final-remediation.frontend.design.md#2.2 功能方案
- **Spec-Refs**: frontend-semi-design#RULE-frontend-semi-001
- **Acceptance-Refs**: S-F3, RULE-frontend-semi-001

### Description

runs 列表＋详情加 status 徽标列（未知值回落灰色）；e2e seed（`agent-editor-lifecycle`/`chat-nfr`/`runtime-profile-contract`）建 profile 去 4 字段；console 全量 vitest＋目标 playwright 回归。

### Checklist
- [ ] [S-F3][E2E] 真浏览器＋dev 后端：四种终态徽标一一对应（S-F3 最终验收负责人）
- [ ] e2e seed 全为合法 v2；console 157＋ 测试全绿；typecheck/lint/约束脚本全过
- [ ] verifier `RULE-frontend-semi-001`：以 S-F3 行为证据验证 Semi 体系与 adapter 首导入
- [ ] 运行验收命令并填写 Acceptance Evidence

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| S-F3 | E2E | 真浏览器＋dev 后端＋PG | 徽标一一对应 | frontend/e2e/（runs 相关＋runtime-profile-contract 更新） | `npx playwright test frontend/e2e/runtime-profile-contract.spec.ts` | planned |
| RULE-frontend-semi-001 | E2E | 同上 | Semi＋adapter，由 S-F3 提供行为证据 | 同上 | 同上 | planned |

### Acceptance Evidence

> `cf-task-start` 在编码期填写 RED/GREEN 结果、每个关键断言的位置和真实组件证据；全部状态 verified 后任务才可 done。

### Log
- [2026-09-08] created (draft)
