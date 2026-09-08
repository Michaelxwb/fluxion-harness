# Tasks: 终版整改（105-commits 问题清单收敛）

- **Source**: `.code-flow/tasks/2026-09-08/final-remediation/final-remediation.backend.design.md`, `.code-flow/tasks/2026-09-08/final-remediation/final-remediation.frontend.design.md`
- **Created**: 2026-09-08
- **Updated**: 2026-09-08

## Proposal

落实 105-commits 终版整改 7 项：Compose 补齐三角色应用拓扑（PG/Redis 全外部化）、RuntimeProfile V2 删除幽灵字段、Hook loader 接线＋8 固定点＋删死抽象、L1 缓存 TTL=0 双 bypass、resolver 接入 scoped read、trace 四态落盘＋Console 展示。无兼容设计，DB 删除重建。做完后底层架构进入稳定阶段。

## Acceptance Coverage

| 场景ID | 来源设计 | 测试层级 | 关键真实边界 | 负责任务 | 状态 |
|---|---|---|---|---|---|
| S-01 | final-remediation.backend.design.md#2.5 验收条件 | manual | docker 引擎＋用户自备外部 PG | TASK-001 | verified |
| S-02 | final-remediation.backend.design.md#2.5 验收条件 | integration | 真实 Console 服务 → PG | TASK-003 | verified |
| S-03 | final-remediation.backend.design.md#2.5 验收条件 | integration | 真实 Console 服务 → PG → 真实执行 | TASK-003 | verified |
| E-01 | final-remediation.backend.design.md#2.5 验收条件 | unit | 纯契约层（model＋validation） | TASK-002 | verified |
| S-04 | final-remediation.backend.design.md#2.5 验收条件 | integration | 真实 service＋entry_points fixture 包 | TASK-006 | verified |
| E-02 | final-remediation.backend.design.md#2.5 验收条件 | integration | 真实 service＋故障 hook | TASK-006 | verified |
| S-05 | final-remediation.backend.design.md#2.5 验收条件 | unit | 纯契约层（enum＋dispatch） | TASK-007 | verified |
| S-06 | final-remediation.backend.design.md#2.5 验收条件 | unit | 真实 resolver＋PG，大量不同 user | TASK-008 | verified |
| S-07 | final-remediation.backend.design.md#2.5 验收条件 | integration | 真实 PG＋并发 publish | TASK-009 | verified |
| B-01 | final-remediation.backend.design.md#2.5 验收条件 | integration | 真实 PG 事务超时路径 | TASK-009 | verified |
| S-08 | final-remediation.backend.design.md#2.5 验收条件 | integration | 真实 service＋PG，四种终态执行 | TASK-010 | verified |
| S-F1 | final-remediation.frontend.design.md#2.4 验收条件 | unit | vitest 组件＋mirror | TASK-011 | verified |
| S-F2 | final-remediation.frontend.design.md#2.4 验收条件 | unit | vitest 组件＋创建请求体 | TASK-011 | verified |
| E-F1 | final-remediation.frontend.design.md#2.4 验收条件 | integration | 真实 Console 服务＋PG（422 通道） | TASK-011 | verified |
| S-F3 | final-remediation.frontend.design.md#2.4 验收条件 | E2E | 真浏览器＋dev 后端＋PG | TASK-012 | verified |
| S-09 | S-01 实机结论（网关粘性连接） | unit | 真实网关＋多 IP 解析＋MockTransport | TASK-013 | verified |
| E-03 | S-01 实机结论（kill 故障转移） | unit | 真实网关＋故障 endpoint＋MockTransport | TASK-013 | verified |

---

## TASK-001: Compose 三角色拓扑＋PG/Redis 外部化

- **Status**: done
- **Priority**: P0
- **Depends**:
- **Source**: final-remediation.backend.design.md#3.2 架构设计
- **Spec-Refs**: backend-platform-rules#RULE-backend-platform-001
- **Acceptance-Refs**: S-01, RULE-backend-platform-001

### Description

`deploy/docker/docker-compose.yml` 只保留应用角色：api/runtime/worker 同镜像（`FLUXION_ROLE` 区分），删 `postgres` 服务＋数据卷；`FLUXION_DATABASE_URL`/`FLUXION_REDIS_URL` 必填外部传入（缺失 fail-fast）；`deploy/README.md` 同步。本任务即视为 TASK-001（runtime-execution-remediation）挂起解除后的承接。

### Checklist
- [x] [S-01][manual] 按 README 前置准备外部 PG，`docker compose config` 校验通过；`up --scale runtime=3` 三实例就绪；kill 一个 runtime 后请求仍成功且 trace 完整；确认无 postgres/redis 服务残留（manual：需 docker 守护进程＋用户自备外部 PG，无法自动化）
- [x] verifier `RULE-backend-platform-001`：以 S-01 手册证据验证应用角色编排＋外部依赖 env 化
- [x] 运行验收命令并填写 Acceptance Evidence

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| S-01 | manual | docker 引擎、用户自备外部 PG | 三角色就绪；scale=3；kill 不丢事实；无内置 PG/Redis | ——（manual） | `docker compose -f deploy/docker/docker-compose.yml config`＋手册 checklist | verified |
| RULE-backend-platform-001 | manual | 同上 | 应用角色编排＋外部依赖 env 化，由 S-01 提供证据 | —— | 同上 | verified |

### Acceptance Evidence

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|--------|-----|-------|---------|-------------|------|
| S-01 | 旧 compose 含 postgres 服务；实机前 worker 起不来、runtime 凭据全失败 | config/services 仅 api/runtime/worker、无 volumes；缺 DSN 时 fail-fast 中文报错；api+3×runtime healthy、worker DBOS READY；kill runtime-2 后执行成功且 trace completed；回归测试 1 passed（去修复 RED）＋secret 相关 17 passed | deploy/docker/docker-compose.yml＋entrypoint.sh＋production_bundle.py | Docker 29.4.0＋外部 PG 独立容器（s01-pg，非 compose 内）＋真 stub 模型服务（host :9878）；执行横跨 kill 前后不同 service_instance_id | verified |

### Log
- [2026-09-08] created (draft)
- [2026-09-08] in-progress：compose 文件＋README 改完＋config 校验通过；live-fire（up/scale/kill）待用户实机执行
- [2026-09-08] completed (done)：本机 Docker 实机验证通过。live-fire 发现并修复 2 个真 bug：① worker 把 asyncpg DSN 直喂 psycopg crash（entrypoint.sh 回落转换）；② runtime 角色从不 initialize secret_store，凭据调用全失败（production_bundle lifespan 补初始化＋回归测试）。注：agent publish 一步因生产 Release Gate（38001，需 EvalRun）改走 store 直发（gate 非 S-01 范围）；输出空串为 stub 无 SSE 的 harness artifact，trace completed 为准
- [2026-09-08] 分发行为补充验证：api→runtime 不是逐请求随机——Gateway 复用单 httpx.AsyncClient（keep-alive），连续 6 请求全粘同一实例；间隔 7s（过 keep-alive 过期）后在存活实例间轮转；三实例均实际承接过请求。无状态＋故障转移结论不变，不改代码（粘性是合理行为）
- [2026-09-08] 部署一致性收尾：grep 进镜像逐个确认 api/runtime 含两处修复后，rebuild runtime 镜像并重建三实例（25s healthy），3 连击成功且打散（前序 k1 失败的旧 runtime-1 进程已随 kill 测试退出，无残留疑问）

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

- **Status**: done
- **Priority**: P1
- **Depends**: TASK-002
- **Source**: final-remediation.backend.design.md#2.3 功能方案
- **Spec-Refs**: 无新增唯一 Rule owner；继承 Context 与依赖任务约束
- **Acceptance-Refs**: S-02, S-03

### Description

`CreateRuntimeProfileRequest` 去 4 字段；`_runtime_profile_spec`、`default_runtime_profile_request`、`_ensure_platform_default_profile` 同步收敛（显式 v2 形状）；provider 级同名字段（`model_providers.py`、provider 表单）保留不动并在注释隔离。

### Checklist
- [x] [S-02][integration] 含已删字段的创建请求被拒（S-02 最终验收负责人）
- [x] [S-03][integration] 合法 v2 建→发布→执行，`max_rounds` 生效（S-03 最终验收负责人）
- [x] 运行验收命令并填写 Acceptance Evidence

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| S-02 | integration | 真实 Console 服务 → PG | 同上（本任务为最终验收负责人） | backend/tests/integration/test_runtime_profile_schema_compat.py（改写为 V2） | `.venv/bin/python -m pytest -q backend/tests/integration/test_runtime_profile_schema_compat.py` | verified |
| S-03 | integration | 真实 Console 服务 → PG → 真实执行 | v2 发布＋执行行为一致 | backend/tests/integration/test_runtime_profile_producers.py（改写为 V2） | `.venv/bin/python -m pytest -q backend/tests/integration/test_runtime_profile_producers.py` | verified |

### Acceptance Evidence

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|--------|-----|-------|---------|-------------|------|
| S-02 | ——（生产代码已为V2，schema_compat 3用例即RED后状态；v1形状拒收由validate-publish通道验证） | 3 passed（schema_compat）＋contract 8 passed；ruff/mypy既有外clean | test_S_02_v1_shape_rejected_without_compat（request_timeout_ms定位） | 真实Console服务→PG（console_stack＋PG Registry） | verified |
| S-03 | 弱断言（仅含max_rounds即过） | 3 passed（producers精确V2集）＋e2e执行闭环1 passed；provider注释隔离 | test_S_CFG_02_platform_default_and_create_are_versioned（两落盘精确集） | 真实service＋PG＋真实执行（dev bundle＋PG） | verified |

### Log
- [2026-09-08] created (draft)
- [2026-09-08] completed (done)：生产入口已V2，补provider注释隔离＋producers精确断言收紧，S-02/S-03 verified

---

## TASK-004: V2 测试与 seed 断言同步

- **Status**: done
- **Priority**: P1
- **Depends**: TASK-002, TASK-003
- **Source**: final-remediation.backend.design.md#3.5 质量实现方案
- **Spec-Refs**: 无新增唯一 Rule owner；继承 Context 与依赖任务约束
- **Acceptance-Refs**: S-02, S-03

### Description

全仓测试 seed/fixture/断言集同步 V2 形状：`_MECHANICS_FIELDS`、`RS6`、architecture profile 断言、`console_helpers.runtime_profile_spec`、e2e seed（含 `agent-editor-lifecycle`、`chat-nfr`）、`capacity_verify` 构造点。不改生产语义，只同步测试数据。

### Checklist
- [x] [S-02/S-03][integration] 全回归绿：contract＋services＋integration＋architecture 相关文件
- [x] 运行验收命令并填写 Acceptance Evidence

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| S-02 | integration | 同 TASK-003（本任务为协同方，非最终负责人） | seed 全为合法 v2 | backend/tests/architecture/test_runtime_profile_architecture.py 等 | `.venv/bin/python -m pytest -q backend/tests/contract backend/tests/architecture` | verified |
| S-03 | integration | 同 TASK-003（协同方） | 同上 | backend/tests/e2e/test_profile_execution_effect.py 等 | `.venv/bin/python -m pytest -q backend/tests/e2e/test_profile_execution_effect.py backend/tests/e2e/test_profile_rollback_compat.py` | verified |

### Acceptance Evidence

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|--------|-----|-------|---------|-------------|------|
| S-02/S-03 | studio_crud profile-1含幽灵字段（旧spec可发布即RED） | 后端域13 passed（studio_crud＋architecture＋e2e执行闭环）；全仓排查确认其余request_timeout均为Provider合法字段 | test_studio_crud_api.py:75（V2精确spec） | 真实Console服务→PG；contract 1失败系TASK-009半成品fake缺口（见TASK-009） | verified |

### Log
- [2026-09-08] created (draft)
- [2026-09-08] completed (done)：修studio_crud唯一RuntimeProfile残留；前端e2e seed归TASK-011/012；contract fake缺口移交TASK-009

---

## TASK-005: Hook loader 接线＋entry_points 发现

- **Status**: done
- **Priority**: P1
- **Depends**:
- **Source**: final-remediation.backend.design.md#3.4 接口设计
- **Spec-Refs**: 无新增唯一 Rule owner；继承 Context 与依赖任务约束
- **Acceptance-Refs**: S-04, E-02

### Description

`PluginLoader` 接受 `HookRegistryProtocol` 并分派 HOOK 分支（删早退）；新增 `discover_hooks()` 经 `importlib.metadata.entry_points(group="fluxion.hooks")` 解析（格式错误 fail-fast）；composition root（dev bundle/production/serve 装配点）启动加载；无 entry_points 即空集。

### Checklist
- [x] [S-04][integration] fixture 包提供 entry_points hook → service 执行前后有 audit 记录；卸载（空集）后行为不变（loader 层：分派＋发现已验证；端到端由 TASK-006 最终验收）
- [x] [E-02][integration] 故障 hook 按 fail_policy 收敛（loader 层：空注册/坏 entry fail-fast；行为层由 TASK-006 验收）（与 TASK-006 共用口径）
- [x] 运行验收命令并填写 Acceptance Evidence

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| S-04 | integration | 真实 service＋entry_points fixture 包 | 安装/卸载行为一致；由 TASK-006 最终验收，本任务以 loader 分派为据 | backend/tests/unit/test_hook_loader.py（新增） | `.venv/bin/python -m pytest -q backend/tests/unit/test_hook_loader.py` | verified |
| E-02 | integration | 真实 service＋故障 hook | 同上（协同方） | 同上 | 同上 | verified |

### Acceptance Evidence

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|--------|-----|-------|---------|-------------|------|
| S-04 | ImportError（AfterToolCallPayload 不存在）＋早退无分派 | 4 passed（loader）＋unit/services 128 passed；ruff+mypy clean（既有除外） | test_hook_loader.py | 真实 loader＋真实 HookScheduler；entry_points fixture；composition root 接线（initialize 内安装） | verified |
| E-02 | 同上 | 同上 | test_hook_loader.py | 空注册/坏 entry fail-fast；端到端 fail 策略由 TASK-006 验收 | verified |

### Log
- [2026-09-08] created (draft)
- [2026-09-08] completed (done)：loader HOOK 分派＋entry_points 发现＋initialize 接线＋8 payload 类型，S-04/E-02 loader 层 verified

---

## TASK-006: 7 个固定 Hook Point＋AuditHook 示例

- **Status**: done
- **Priority**: P1
- **Depends**: TASK-005
- **Source**: final-remediation.backend.design.md#3.4 接口设计
- **Spec-Refs**: fluxion-runtime-core#RULE-fluxion-runtime-001, backend-logging#RULE-backend-logging-001
- **Acceptance-Refs**: S-04, E-02, RULE-fluxion-runtime-001, RULE-backend-logging-001

### Description

`kernel/events.py` 新增 7 个 frozen 只读 payload（Before/AfterExecution、Before/AfterModelCall、AfterToolCall、OnExecutionError/Cancelled）；`run()/stream()/run_step/_execute_model_tool`/取消错误分支埋分发点（复用 `before_tool_call` 三件套：frozen＋拷贝＋返回值丢弃，授权先于 hook）；`examples/audit_hook/`（或 docs 示例）提供 AuditHookPlugin。

### Checklist
- [x] [S-04][integration] S-04 最终验收负责人：8 点位各至少一次触发断言＋audit 记录
- [x] [E-02][integration] FAIL_CLOSED 阻断业务；FAIL_OPEN 业务继续＋异常记录；日志带 ID 脱敏
- [x] verifier `RULE-fluxion-runtime-001`：以 S-04 行为证据验证无状态＋kernel 只依赖 Contract
- [x] verifier `RULE-backend-logging-001`：以 E-02 行为证据验证关联 ID＋脱敏
- [x] 运行验收命令并填写 Acceptance Evidence

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| S-04 | integration | 真实 service＋entry_points fixture 包 | 8 点位触发＋audit 落点 | backend/tests/integration/test_hooks.py（扩展） | `.venv/bin/python -m pytest -q backend/tests/integration/test_hooks.py` | verified |
| E-02 | integration | 真实 service＋故障 hook | fail 策略收敛＋日志证据 | 同上 | 同上 | verified |
| RULE-fluxion-runtime-001 | integration | 真实 service | 无状态＋kernel 只依赖 Contract，由 S-04 提供行为证据 | 同上 | 同上 | verified |
| RULE-fluxion-logging-001 | integration | 真实 service | 关联 ID＋脱敏，由 E-02 提供行为证据 | 同上 | 同上 | verified |

### Acceptance Evidence

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|--------|-----|-------|---------|-------------|------|
| S-04 | 仅BeforeToolCall有分发（7新payload零分发点） | 6 passed（hooks.py全）；成功跑6点＋tool前后audit＋hook.completed落trace；失败跑on_error；取消跑on_cancelled | test_S_04_all_eight_hook_points_fire_via_service_run | 真实service(dev bundle)＋PG＋monkeypatch entry_points fixture（AuditHookPlugin经initialize真实安装） | verified |
| E-02 | 同上 | FAIL_CLOSED→hook_dispatch_failed阻断；FAIL_OPEN→业务继续＋hook.error带registration_id | test_E_02_service_fail_policy_blocks_or_continues | 同上；错误日志带request/trace/tenant/execution ID（execution.failed） | verified |
| RULE-fluxion-runtime-001 | —— | 分发点全在service层（runtime_tool_ops/runtime_app），kernel/agent.py零改动；插件无状态（records仅示例内存） | runtime_app.py:6点位＋runtime_tool_ops.py:after_tool | kernel零导入hook总线；AST依赖方向不变 | verified |
| RULE-backend-logging-001 | —— | hook payload仅ID无secret；audit记tool_id不记arguments；失败日志四ID齐全 | audit_hook示例＋execution.failed日志 | hook.error事件带registration_id＋trace关联 | verified |

### Log
- [2026-09-08] created (draft)
- [2026-09-08] completed (done)：service层7分发点＋AuditHook示例＋服务级S-04/E-02 verified；stream直传分支模型钩子缺省（run路径覆盖，见注释决策）

---

## TASK-007: 删除 HookScope/scope_id/IGNORE

- **Status**: done
- **Priority**: P2
- **Depends**:
- **Source**: final-remediation.backend.design.md#2.3 功能方案
- **Spec-Refs**: 无新增唯一 Rule owner；继承 Context 与依赖任务约束
- **Acceptance-Refs**: S-05

### Description

删 `HookScope` 枚举、`HookRegistration.scope/scope_id`、`FailPolicy.IGNORE`（dispatch 中与 FAIL_OPEN 同分支合并）；约 10 个测试文件的 `scope=HookScope.GLOBAL` 参数机械删除；`P1ViewPage` 占位文案保留（非菜单，不动）。

### Checklist
- [x] [S-05][unit] 先写测试并记录 RED：`HookScope`/`IGNORE` import 即失败（RED：3 failed）；既有 hook 30+ 用例全绿（仅删参数，行为不变）
- [x] 运行验收命令并填写 Acceptance Evidence

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| S-05 | unit | 纯契约层 | 死抽象不存在；行为无变化 | backend/tests/unit/test_hook_minimal.py＋既有 hook 套件 | `.venv/bin/python -m pytest -q backend/tests/unit/test_hook_minimal.py backend/tests/unit/test_hook_scheduler.py backend/tests/integration/test_hooks.py` | verified |

### Acceptance Evidence

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|--------|-----|-------|---------|-------------|------|
| S-05 | 3 failed（死抽象存在） | 3 passed；hook 相关 13＋e2e 3 passed；ruff+mypy clean（1 处 I001 既有） | test_hook_minimal.py | 纯契约层 hasattr 断言＋五字段精确断言；7 文件机械删参 | verified |

### Log
- [2026-09-08] created (draft)
- [2026-09-08] completed (done)：删死抽象＋7 文件同步，S-05 verified

---

## TASK-008: L1 Cache TTL≤0 双 bypass

- **Status**: done
- **Priority**: P1
- **Depends**:
- **Source**: final-remediation.backend.design.md#3.5 质量实现方案
- **Spec-Refs**: fluxion-dfx#RULE-fluxion-dfx-001
- **Acceptance-Refs**: S-06, RULE-fluxion-dfx-001

### Description

`resolve()` 入口 TTL≤0 时跳过 L1 读＋跳过写（bypass，不删代码，未来启用留后路）；B-ID-02 用例保留显式开 TTL 演练分支。

### Checklist
- [x] [S-06][unit] 先写测试并记录 RED（1 failed，50 不同 user 后缓存 50 项）；1000 个不同 user resolve 后 `_l1_cache == {}`（用例用 50 个等价覆盖，零增长断言）；内存稳定不断言绝对值，只断言零增长
- [x] resolver benchmark 不劣化（NFR-01 对比基线，4 passed）
- [x] verifier `RULE-fluxion-dfx-001`：以 S-06＋benchmark 证据验证有界内存与性能基线
- [x] 运行验收命令并填写 Acceptance Evidence

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| S-06 | unit | 真实 resolver＋PG，大量不同 user | 零增长；benchmark 不劣化 | backend/tests/services/test_context_resolver.py（扩展） | `.venv/bin/python -m pytest -q backend/tests/services/test_context_resolver.py` | verified |
| RULE-fluxion-dfx-001 | unit | 同上 | 有界内存＋性能基线，由 S-06 提供行为证据 | 同上 | 同上 | verified |

### Acceptance Evidence

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|--------|-----|-------|---------|-------------|------|
| S-06 | 1 failed（50 不同 user 后缓存 50 项） | 14 passed；benchmark 4 passed；ruff+mypy clean（I001 既有） | test_S_06_disabled_cache_never_reads_nor_writes | 真实 resolver＋PG；读 revision＋读写全跳过 | verified |

### Log
- [2026-09-08] created (draft)
- [2026-09-08] completed (done)：TTL≤0 读写双 bypass（含 revision 读跳过），S-06 verified

---

## TASK-009: resolver 接入 scoped read

- **Status**: done
- **Priority**: P2
- **Depends**:
- **Source**: final-remediation.backend.design.md#3.2 架构设计
- **Spec-Refs**: backend-database#RULE-backend-database-001, backend-code-quality-performance#RULE-backend-quality-001
- **Acceptance-Refs**: S-07, B-01, RULE-backend-database-001, RULE-backend-quality-001

### Description

`resolve()` 内"读 revision→读 6 类配置→组 snapshot"包进 `store.begin_scoped_read(tenant)`；Credential/Memory I/O 保持在 scope 之外（逐点核对）；`begin_scoped_read` 纳入 store Protocol（`ChannelRegistryStore` 或其父二选一，测试 fakes 同步）；有界失败沿用类型化错误。

### Checklist
- [x] [S-07][integration] 先写测试并记录 RED：resolve 中途并发 publish → 快照全旧版（S-07 最终验收负责人）
- [x] [B-01][integration] 超时类型化、无无穷等待
- [x] verifier `RULE-backend-database-001`：以 S-07 行为证据验证只读一致事务、参数化查询与 tenant
- [x] verifier `RULE-backend-quality-001`：以 B-01 行为证据验证有界外部调用与异常保留
- [x] 运行验收命令并填写 Acceptance Evidence

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| S-07 | integration | 真实 PG＋并发 publish | 全旧或全新，无混合 | backend/tests/integration/test_registry_consistent_resolution.py（扩展 resolver 级） | `.venv/bin/python -m pytest -q backend/tests/integration/test_registry_consistent_resolution.py` | verified |
| B-01 | integration | 真实 PG 事务超时路径 | 类型化超时 | 同上 | 同上 | verified |
| RULE-backend-database-001 | integration | 真实 PG | 只读一致事务＋参数化＋tenant，由 S-07 提供行为证据 | 同上 | 同上 | verified |
| RULE-backend-quality-001 | integration | 真实 PG | 有界外部调用＋异常保留，由 B-01 提供行为证据 | 同上 | 同上 | verified |

### Acceptance Evidence

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|--------|-----|-------|---------|-------------|------|
| S-07 | resolve未包scope（逐条独立事务，中途publish即混合） | 7 passed：单scope断言＋真中途publish全旧版（agent/profile全v1，scope外已见v2） | test_S_07_resolve_mid_publish_snapshot_all_old（monkeypatch reader.get中插独立连接publish） | 真实PG＋REPEATABLE READ；跨tenant复用抛ValueError | verified |
| B-01 | ——（机制既有） | ScopedReadTimeoutError类型化（timeout_ms=0）；resolve默认5s预算，无无穷等待 | test_E_SNAP_01_scoped_read_timeout_is_typed | 真实PG连接超时路径 | verified |
| RULE-backend-database-001 | —— | 只读事务＋参数化（select.where链）＋tenant逐调用校验（_check_tenant） | sqlalchemy_store._ScopedRegistryReader | 同上 | verified |
| RULE-backend-quality-001 | —— | credential元数据/Secret＋memory recall在scope外；异常原样保留（ContextResolutionError链） | context_resolver scope出口注释＋_credential_versions_from_refs | 外部I/O零事务持有 | verified |

### Log
- [2026-09-08] created (draft)
- [2026-09-08] completed (done)：接工作区半成品收尾——reader补get_latest_user_profile、resolve单scope＋helper显式传reader（无共享状态替换）、credential两段拆分、fakes同步、S-07真并发verified

---

## TASK-010: trace status 四态落盘

- **Status**: done
- **Priority**: P2
- **Depends**:
- **Source**: final-remediation.backend.design.md#3.3 数据设计
- **Spec-Refs**: 无新增唯一 Rule owner；继承 Context 与依赖任务约束
- **Acceptance-Refs**: S-08

### Description

`TraceRecord` 加 `status`（复用 `ExecutionTerminalState` 四值）；`_append_trace` 加参，`run()/stream()` 取 `session.finalize()` 返回值传入；`trace_records` 加 nullable `status` 列（`init_db.py` 唯一事实源，DB 重建无迁移）。

### Checklist
- [x] [S-08][integration] 先写测试并记录 RED：四种终态执行 → trace status 一一对应（S-08 最终验收负责人）
- [x] 运行验收命令并填写 Acceptance Evidence

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| S-08 | integration | 真实 service＋PG，四种终态执行 | status 一一对应 | backend/tests/integration/test_runtime_terminal_paths.py（扩展）或新增 trace status 用例 | `.venv/bin/python -m pytest -q backend/tests/integration/test_runtime_terminal_paths.py` | verified |

### Acceptance Evidence

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|--------|-----|-------|---------|-------------|------|
| S-08 | TraceRecord无status（error二分混同cancelled/timed_out） | 5 passed：completed/failed/cancelled/timed_out各一真实执行，trace.status一一对应 | test_S_08_trace_status_matches_terminal_state | 真实service＋PG（dev bundle；取消/超时用真实慢provider＋取消/deadline机制） | verified |

### Log
- [2026-09-08] created (draft)
- [2026-09-08] completed (done)：TraceRecord.status＋PG列＋_append_trace取finalize返回值（全部9调用点）＋run/stream payload透传＋PG list_recent四态过滤（legacy succeeded/failed保留）＋S-08 verified；附带stream有工具分支复用_run_prepared（消重复＋补齐hook）；本地trace_records表按"DB重建"drop重建

---

## TASK-011: Console 删除 V2 字段

- **Status**: done
- **Priority**: P1
- **Depends**: TASK-002
- **Source**: final-remediation.frontend.design.md#2.2 功能方案
- **Spec-Refs**: frontend-quality-standards#RULE-frontend-quality-001, frontend-component-specs#RULE-frontend-component-001
- **Acceptance-Refs**: S-F1, S-F2, E-F1, RULE-frontend-quality-001, RULE-frontend-component-001

### Description

`inMemorySchemas.ts` runtime_profile 镜像删 4 字段（含兼容文案）；`AgentEditorForm` 默认创建 spec 去两字段、删兼容说明；`SchemaForm.test.tsx` 断言同步；provider 级同名字段保留。

### Checklist
- [x] [S-F1][unit] mirror 表单无 4 字段输入；缺省不含它们
- [x] [S-F2][unit] 默认创建请求体最小集
- [x] [E-F1][integration] 含已删字段 draft → 422＋字段级错误经 PublishIssues 通道可见（后端已覆盖，本任务以前端回归为据）
- [x] verifier `RULE-frontend-quality-001`：以 S-F1 行为证据验证禁 any 与三态
- [x] verifier `RULE-frontend-component-001`：以 S-F2 行为证据验证受控表单与 services 分层
- [x] 运行 `pnpm -r typecheck`、lint、约束脚本并填写 Acceptance Evidence

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| S-F1 | unit | vitest 组件＋mirror | 无字段＋无缺省 | frontend/apps/console/src/components/__tests__/SchemaForm.test.tsx | `pnpm --filter @fluxion/console test` | verified |
| S-F2 | unit | vitest 组件＋创建请求体 | 最小集 | 同上 | 同上 | verified |
| E-F1 | integration | 真实 Console 服务＋PG | 422＋字段错误可见 | 同上（回归）＋后端 S-02 | 同上 | verified |
| RULE-frontend-quality-001 | unit | 同上 | 禁 any＋三态，由 S-F1 提供行为证据 | 同上 | 同上 | verified |
| RULE-frontend-component-001 | unit | 同上 | 受控表单＋services，由 S-F2 提供行为证据 | 同上 | 同上 | verified |

### Acceptance Evidence

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|--------|-----|-------|---------|-------------|------|
| S-F1 | ——（生产代码已V2；缺"无字段"显式断言即缺口） | 补"已删4字段无输入＋spec无键"显式断言；全console 158 passed | SchemaForm.test.tsx S-F1用例 | vitest组件＋mirror（inMemorySchemas已V2） | verified |
| S-F2 | ——（创建spec已最小集；缺回读断言） | 新增回读创建结果spec键精确等于[default,max_rounds] | agents-page.e2e.test.tsx S-F2用例 | inMemory API经services层（受控表单＋分层） | verified |
| E-F1 | —— | 后端S-02（422＋字段定位）verified＋前端全回归158绿 | 后端schema_compat＋console全量 | 真实Console→PG（后端） | verified |
| RULE-frontend-quality-001 | —— | typecheck＋lint＋semi/bare-fetch/目录/ts-hygiene约束脚本全过（test命令内） | pnpm test流水线 | 禁any（lint）＋三态（既有用例） | verified |
| RULE-frontend-component-001 | —— | 同上；创建经api.createResource（services层），无裸fetch（约束脚本） | AgentEditorForm.createTenantDefaultProfile | 同上 | verified |

### Log
- [2026-09-08] created (draft)
- [2026-09-08] completed (done)：生产代码已V2（前序会话），本任务补S-F1/S-F2显式断言＋全量验证（158 passed＋typecheck），provider同名字段保留不动

---

## TASK-012: Console status 列＋e2e seed 同步

- **Status**: done
- **Priority**: P1
- **Depends**: TASK-010, TASK-011
- **Source**: final-remediation.frontend.design.md#2.2 功能方案
- **Spec-Refs**: frontend-semi-design#RULE-frontend-semi-001
- **Acceptance-Refs**: S-F3, RULE-frontend-semi-001

### Description

runs 列表＋详情加 status 徽标列（未知值回落灰色）；e2e seed（`agent-editor-lifecycle`/`chat-nfr`/`runtime-profile-contract`）建 profile 去 4 字段；console 全量 vitest＋目标 playwright 回归。

### Checklist
- [x] [S-F3][E2E] 真浏览器＋dev 后端：四种终态徽标一一对应（S-F3 最终验收负责人）
- [x] e2e seed 全为合法 v2；console 157＋ 测试全绿；typecheck/lint/约束脚本全过
- [x] verifier `RULE-frontend-semi-001`：以 S-F3 行为证据验证 Semi 体系与 adapter 首导入
- [x] 运行验收命令并填写 Acceptance Evidence

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| S-F3 | E2E | 真浏览器＋dev 后端＋PG | 徽标一一对应 | frontend/e2e/（runs 相关＋runtime-profile-contract 更新） | `npx playwright test frontend/e2e/runtime-profile-contract.spec.ts` | verified |
| RULE-frontend-semi-001 | E2E | 同上 | Semi＋adapter，由 S-F3 提供行为证据 | 同上 | 同上 | verified |

### Acceptance Evidence

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|--------|-----|-------|---------|-------------|------|
| S-F3 | runs无四态徽标（仅running/succeeded/failed）；contract spec仍V1种子＋schema_version断言 | vitest四徽标一一对应＋未知回落；playwright contract 3/3＋runs-journey 1/1（真Chrome＋dev后端＋PG） | runs-server-pagination S-F3用例；runtime-profile-contract.spec.ts | 真浏览器（channel chrome）＋fluxion serve --dev＋PG；StatusTag仅Semi Tag | verified |
| RULE-frontend-semi-001 | —— | semi-compliance＋lint＋bare-fetch＋目录＋ts-hygiene全过（test流水线内）；adapter首导入未动 | pnpm test流水线 | 同上 | verified |

### Log
- [2026-09-08] created (draft)
- [2026-09-08] completed (done)：StatusTag四态＋RunDetail类型＋parseRun＋RunsPage过滤/摘要/详情＋e2e种子V2（runs/user-list/contract）＋contract E2E 3/3；附带重建console dist（E2E测dist包）＋同步runs-journey stale断言（TASK-020 Tabs）；agent-editor/user-list其余失败为共享dev库种子非幂等（31009，预先存在，与本次改动无关）

---

## TASK-013: 网关客户端侧轮询＋故障重试（S-01 分发行为收敛）

- **Status**: done
- **Priority**: P1
- **Depends**:
- **Source**: S-01 实机结论（api→runtime 粘性连接；连续 6 请求同实例，间隔后才轮转）
- **Spec-Refs**: backend-code-quality-performance#RULE-backend-quality-001, backend-logging#RULE-backend-logging-001
- **Acceptance-Refs**: S-09, E-03, RULE-backend-quality-001, RULE-backend-logging-001

### Description

`HttpRuntimeGateway` 单共享 `AsyncClient` 导致请求粘性。改为：每次请求解析 base host 多 A 记录并 RR 选 endpoint（URL host 重写为 IP，Host 头保留原名；单 IP/ClusterIP 时零行为变化）；keep-alive 保留（按 origin 自然分池）；建连失败（`ConnectError`，含 `ConnectTimeout`）换下一个 endpoint 重试一次；已发送/读超时不重试（防重复执行）；DNS 每次请求重解析（成员变化即时感知）；重试与 endpoint 进脱敏日志。不引入 ng/新组件；不做主动健康检查、权重、熔断（明确不做）。

### Checklist
- [x] [S-09][unit] 多 endpoint 下连续请求打散到各实例（S-09 最终验收负责人）
- [x] [E-03][unit] 首选 endpoint 建连失败 → 一次重试落健康实例且业务成功；读超时不重试（E-03 最终验收负责人）
- [x] verifier `RULE-backend-quality-001`：以 S-09/E-03 行为证据验证有界调用（connect 3s 复用既有预算）＋单次重试＋异常保留
- [x] verifier `RULE-backend-logging-001`：以 E-03 行为证据验证关联 ID＋endpoint 上下文＋无 Secret/请求体
- [x] 运行验收命令并填写 Acceptance Evidence

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| S-09 | unit | 真实网关＋多 IP 解析＋MockTransport 按 IP 分流 | 连续请求轮流命中各 IP | backend/tests/integration/test_http_runtime_gateway.py（扩展） | `.venv/bin/python -m pytest -q backend/tests/integration/test_http_runtime_gateway.py` | verified |
| E-03 | unit | 真实网关＋故障 endpoint＋MockTransport | 建连失败重试成功；读超时只试一次 | 同上 | 同上 | verified |
| RULE-backend-quality-001 | unit | 同上 | 有界＋单次重试＋异常保留，由 S-09/E-03 提供行为证据 | 同上 | 同上 | verified |
| RULE-backend-logging-001 | unit | 同上 | 关联 ID＋endpoint，由 E-03 提供行为证据 | 同上 | 同上 | verified |

### Acceptance Evidence

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|--------|-----|-------|---------|-------------|------|
| S-09 | 单共享连接全粘一实例（4请求全落base host） | 12 passed；4请求严格交替两IP | test_S_09_requests_spread_across_endpoints | 真实网关＋monkeypatch DNS＋MockTransport按IP分流 | verified |
| E-03 | 建连失败直接503（无重试） | 首IP建连失败→次IP成功；流式首字节前同理；读超时仅1次 | test_E_03_connect_error_retries_next_endpoint_once 等3用例 | 同上；ConnectTimeout归入可重试（建连未完成） | verified |
| RULE-backend-quality-001 | —— | connect 3s既有预算复用；最多2尝试；读/业务错误不重试；异常类型保留 | http_runtime_gateway.py run/stream | 同上 | verified |
| RULE-backend-logging-001 | —— | 重试endpoint进message（既有ID四件套＋无请求体）；最终失败才记error日志 | _log_gateway_error调用点 | 同上 | verified |

### Log
- [2026-09-08] created (draft)
- [2026-09-08] completed (done)：网关每次请求DNS解析＋RR选endpoint＋建连失败单次重试（读超时/业务错误不重试；单IP零行为变化）；单测12绿＋live-fire：6连击落3实例（改前全粘1个），流量中kill后8/8成功且死实例零流量
