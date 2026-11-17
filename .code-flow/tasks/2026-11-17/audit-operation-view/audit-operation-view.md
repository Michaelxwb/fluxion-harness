---
{"version":1,"requirement":"audit-operation-view"}
---
# Tasks: audit-operation-view

## TASK-001: 后端 — RUN 级投影、操作视图参数与索引

- **Status**: done
- **Depends**:
- **Source**: audit-operation-view.backend.design.md#3.3 数据设计
- **Scenarios**: S-01, S-05
- **Spec-Refs**: api/harness-api#RULE-api-001, persistence/harness-data#RULE-data-001
- **Paths**: apps/console-platform/backend/src/muad_console_platform/api/audits.py, apps/console-platform/backend/src/muad_console_platform/application/audit_query_service.py, apps/console-platform/backend/src/muad_console_platform/infrastructure/repositories/audit_query_repository.py, apps/agent-runtime/src/muad_agent_runtime/infrastructure/models/runtime.py, migrations/versions/0051_audit_operation_view_index.py, tests/console_platform/test_audit_query_api.py, tests/console_platform/test_audits_api.py
- **E2E**:
- **Skip-Tests**:
- **Skip-Reason**:

### Tests

```json
[
  {
    "id": "audit-view",
    "argv": ["uv", "run", "python", "-m", "pytest", "-q", "tests/console_platform/test_audit_query_api.py", "tests/console_platform/test_audits_api.py"],
    "timeout": 300,
    "parallel": true
  }
]
```

### Description

在既有四表投影中新增 `RUN` 分支（来源 `runtime.run_record`），把列表默认视图收敛为「一次 Run / 一次配置变更 / 一次出网」；新增 `view=operation|detail` 参数与解析矩阵；结果态补充 `RUNNING` / `CANCELLED`；新增支撑租户 + 时间倒序分页的索引迁移。步骤聚合不在本任务内（TASK-002）。

### Checklist

- [x] 新增迁移 `0051`：`runtime.run_record (tenant_id, create_time DESC) WHERE is_deleted = false`，可回滚
- [x] 新增 RUN 投影分支，字段映射与设计 3.3 一致（含 `summary`、`action` 取入口渠道、耗时按起止时间计算）
- [x] `view` 解析矩阵落地；`view=operation` + 模型/工具类型返回校验错误；未传 `view` 时按类型推断
- [x] 结果态映射补 `RUNNING` / `CANCELLED`，并加入筛选允许集合
- [x] 既有四个分支的 SQL、字段与过滤行为不变
- [x] 列表出参先返回 `summary` 与占位 `steps=null` / `failure=null`，结构冻结供 TASK-002 填充

### Spec Delta

```json
[]
```

### Log

- [2026-11-17] created
- [2026-11-17T11:47:58.094Z] started tests=8a38b9e4f2da

### Verification

- 尚未执行

---
- 2026-11-17T11:57:36.096Z PASS commands=1/1 executed=1 deduped=0 elapsed=11924ms tests=8a38b9e4f2da head=46596c887797 dirty=1
  - audit-view PASS exit=0
## TASK-002: 后端 — 内部步骤两阶段聚合与降级

- **Status**: done
- **Depends**: TASK-001
- **Source**: audit-operation-view.backend.design.md#3.3 数据设计
- **Scenarios**: S-02, E-02, B-03
- **Spec-Refs**: test/harness-test#RULE-test-001
- **Paths**: apps/console-platform/backend/src/muad_console_platform/application/audit_query_service.py, apps/console-platform/backend/src/muad_console_platform/infrastructure/repositories/audit_query_repository.py, tests/console_platform/test_audit_query_api.py
- **E2E**:
- **Skip-Tests**:
- **Skip-Reason**:

### Tests

```json
[
  {
    "id": "audit-steps",
    "argv": ["uv", "run", "python", "-m", "pytest", "-q", "tests/console_platform/test_audit_query_api.py"],
    "timeout": 300,
    "parallel": true
  }
]
```

### Description

在操作视图分页之后，按当页 `run_id` 集合做两次小查询：一次计数（模型/工具/出网 + 模型重试数），一次最近失败步骤；失败摘要只在 Run 自身失败时回填。聚合失败可见降级：`steps=null` + warning 日志，不返回 0。禁止逐行相关子查询与每页全量预聚合。

### Checklist

- [x] 计数查询只接受当页 `run_ids`，使用 `run_id` 前缀索引
- [x] 重试数按模型 attempt 统计，成功但有重试的 Run 不产生失败摘要
- [x] 失败摘要取该 Run 时间上最后一条失败步骤（模型/工具/出网三类的失败判据分开）
- [x] 聚合异常时 `steps=null` 且写 warning（含租户与当页 run 数）
- [x] 非 RUN 行不触发聚合、字段保持 `null`
- [x] 集成用例使用真实 PostgreSQL，覆盖计数、失败、重试、降级四条路径

### Spec Delta

```json
[]
```

### Log

- [2026-11-17] created
- [2026-11-17T11:59:41.424Z] started tests=be4151dd3141

### Verification

- 尚未执行

---
- 2026-11-17T12:00:03.927Z PASS commands=1/1 executed=1 deduped=0 elapsed=14662ms tests=be4151dd3141 head=e50268616e2d dirty=1
  - audit-steps PASS exit=0
## TASK-003: 后端 — 明细视图、导出口径与详情边界

- **Status**: done
- **Depends**: TASK-001
- **Source**: audit-operation-view.backend.design.md#3.4 接口设计
- **Scenarios**: S-03, S-06, B-01, B-02
- **Spec-Refs**:
- **Paths**: apps/console-platform/backend/src/muad_console_platform/api/audits.py, apps/console-platform/backend/src/muad_console_platform/application/audit_query_service.py, apps/console-platform/backend/src/muad_console_platform/application/audit_export_service.py, apps/console-platform/backend/src/muad_console_platform/infrastructure/repositories/audit_query_repository.py, tests/console_platform/test_audit_query_api.py, tests/console_platform/test_audits_api.py, tests/console_platform/test_audit_export_api.py, tests/console_platform/test_audit_export_download.py, tests/console_platform/test_audit_detail_api.py
- **E2E**:
- **Skip-Tests**:
- **Skip-Reason**:

### Tests

```json
[
  {
    "id": "audit-detail-export",
    "argv": ["uv", "run", "python", "-m", "pytest", "-q", "tests/console_platform/test_audit_query_api.py", "tests/console_platform/test_audits_api.py", "tests/console_platform/test_audit_export_api.py", "tests/console_platform/test_audit_export_download.py", "tests/console_platform/test_audit_detail_api.py"],
    "timeout": 300,
    "parallel": true
  }
]
```

### Description

收敛三种读口径：明细视图（`view=detail`）保持改造前的逐条事件行为；导出固定全量明细、忽略视图且不新增 `summary` 列；审计详情只接受四类来源表，`RUN` 返回校验错误。分页与 `total` 按当前视图的行语义计算，只有 `task_id` 的记录在明细视图可见。

### Checklist

- [x] `view=detail` 未传类型时返回四类全量，字段与分页语义同改造前
- [x] `view=detail` + 工具/模型类型返回对应事件行
- [x] 导出服务固定按明细取数，忽略 `view`；`audit_type=RUN` 返回校验错误
- [x] 导出列集合不包含 `summary`
- [x] 详情接口对 `RUN` 返回校验错误，四类来源表行为不变
- [x] 两个视图的 `total` 各自按行语义计数，翻页不越界
- [x] 集成用例覆盖上述六条

### Spec Delta

```json
[]
```

### Log

- [2026-11-17] created
- [2026-11-17T12:00:12.676Z] started tests=69dd81a2bdb3

### Verification

- 尚未执行

---
- 2026-11-17T12:02:23.000Z PASS commands=1/1 executed=1 deduped=0 elapsed=21851ms tests=16be92345f1f head=d3e145de08cd dirty=1 tests-changed
  - audit-detail-export PASS exit=0
## TASK-004: 前端 — 操作视图、明细视图与文案

- **Status**: done
- **Depends**: TASK-003
- **Source**: audit-operation-view.frontend.design.md#3.3 组件设计
- **Scenarios**: S-01, S-02, S-03, S-04, S-05, E-01
- **Spec-Refs**: ui/harness-ui#RULE-ui-001, frontend/harness-frontend#RULE-front-001, i18n/harness-i18n#RULE-i18n-001, time/harness-time#RULE-time-001
- **Paths**: apps/console-platform/frontend/src/modules/audit-observability/types.ts, apps/console-platform/frontend/src/modules/audit-observability/services/auditService.ts, apps/console-platform/frontend/src/modules/audit-observability/hooks/useAuditList.ts, apps/console-platform/frontend/src/modules/audit-observability/components/AuditTable.tsx, apps/console-platform/frontend/src/modules/audit-observability/components/AuditFilterBar.tsx, apps/console-platform/frontend/src/modules/audit-observability/components/AuditViewNotice.tsx, apps/console-platform/frontend/src/modules/audit-observability/pages/AuditPage.tsx, apps/console-platform/frontend/src/modules/audit-observability/auditLabels.ts, apps/console-platform/frontend/src/locales/zh-CN.json, apps/console-platform/frontend/src/locales/en-US.json, e2e/tests/audit-observability-ui.spec.ts, e2e/tests/audit-observability.spec.ts
- **E2E**: e2e/tests/audit-observability-ui.spec.ts
- **Skip-Tests**:
- **Skip-Reason**:

### Tests

```json
[
  {
    "id": "frontend-typecheck",
    "argv": ["npm", "--prefix", "apps/console-platform/frontend", "run", "typecheck"],
    "timeout": 300,
    "parallel": true
  }
]
```

### Description

前端按筛选派生视图并显式传 `view`：默认操作视图 9 列（新增「内部步骤」），工具/模型类型切到 8 列明细视图并显示提示条与返回入口；Run 行首列与对象都打开既有运行详情；失败、重试、降级、进行中、已取消各有明确呈现；同步 zh-CN / en-US 词条与既有审计 E2E 规格。

### Checklist

- [x] 类型与服务层新增 `RUN` 类型、`view` 参数、`summary` / `steps` / `failure`
- [x] 视图派生：工具/模型类型 → 明细，其余 → 操作；切换视图重置到第 1 页
- [x] 操作视图新增「内部步骤」列，计数省略 0 类型，全部为 0 显示 `-`
- [x] 失败摘要仅在 `failure` 非空时出现；重试以中性后缀展示
- [x] 明细视图提示条（可读屏）+「返回操作视图」恢复筛选
- [x] Run 行首列与对象均打开运行详情；非 RUN 行行为不变
- [x] 结果标签支持进行中 / 已取消
- [x] zh-CN / en-US 词条齐备；`npm run typecheck` 与 `vite build` 通过
- [x] 更新两个审计 E2E 规格以匹配新默认视图（不作为 CI 门禁）

### Spec Delta

```json
[
  {
    "id": "DELTA-001",
    "op": "add",
    "target": "ui/harness-ui",
    "rule": "运行审计列表默认按业务操作成行：一次 Run、一次配置变更、一次出网各占一行；模型调用与工具调用的计数与失败在运行详情的概览中查看，列表不重复展示步骤明细。按模型或工具调用筛选时进入步骤明细视图（一行 = 一次调用），列表必须给出当前粒度的可见提示与返回操作视图的入口。审计导出始终输出全量明细，不随列表视图改变粒度。",
    "evidence": "audit-operation-view.backend.design.md#3.4 接口设计；audit-operation-view.frontend.design.md#3.6 UI 状态"
  }
]
```

### Log

- [2026-11-17] created
- [2026-11-17T12:02:30.578Z] started tests=34f97d99fbc1

### Verification

- 尚未执行
- 2026-11-17T12:06:21.326Z PASS commands=1/1 executed=1 deduped=0 elapsed=2532ms tests=34f97d99fbc1 head=6c1792a089bb dirty=1
  - frontend-typecheck PASS exit=0

---

## TASK-005: 步骤统计移入运行详情概览，列表视觉收口

- **Status**: done
- **Depends**: TASK-004
- **Source**: audit-operation-view.frontend.design.md#3.6 UI 状态
- **Scenarios**: S-01, S-02, S-04
- **Spec-Refs**: ui/harness-ui#RULE-ui-001
- **Paths**: apps/console-platform/backend/src/muad_console_platform/application/run_query_service.py, apps/console-platform/backend/src/muad_console_platform/application/audit_query_service.py, apps/console-platform/backend/src/muad_console_platform/infrastructure/repositories/run_query_repository.py, apps/console-platform/backend/src/muad_console_platform/infrastructure/repositories/audit_query_repository.py, apps/console-platform/frontend/src/modules/audit-observability/components/AuditTable.tsx, apps/console-platform/frontend/src/modules/audit-observability/types.ts, apps/console-platform/frontend/src/modules/audit-observability/services/auditService.ts, apps/console-platform/frontend/src/modules/run-observability/RunDetailSideSheet.tsx, apps/console-platform/frontend/src/modules/run-observability/services/runs.ts, apps/console-platform/frontend/src/locales/zh-CN.json, apps/console-platform/frontend/src/locales/en-US.json, apps/console-platform/frontend/src/styles/app.css, tests/console_platform/test_runs_api.py, tests/console_platform/test_audit_query_api.py, e2e/tests/audit-observability-ui.spec.ts
- **E2E**: e2e/tests/audit-observability-ui.spec.ts
- **Skip-Tests**:
- **Skip-Reason**:

### Tests

```json
[
  {
    "id": "run-steps",
    "argv": ["uv", "run", "python", "-m", "pytest", "-q", "tests/console_platform/test_runs_api.py", "tests/console_platform/test_audit_query_api.py"],
    "timeout": 300,
    "parallel": true
  },
  {
    "id": "frontend-typecheck",
    "argv": ["npm", "--prefix", "apps/console-platform/frontend", "run", "typecheck"],
    "timeout": 300,
    "parallel": true
  }
]
```

### Description

列表不再展示「内部步骤」列：步骤计数（模型/工具/出网/重试）改由运行详情接口返回，并在「基本信息」页签展示；列表侧移除聚合与失败摘要字段，保留失败行的红色标记与结果标签。视觉收口：单元格垂直居中、对象短 id 等宽（来源 chip 对齐）、时间列拆成「日期 + 时刻 + 耗时」三级层次。入口语义：Trace ID 下钻该 Trace 的全部记录，关联对象打开运行详情。

### Checklist

- [x] 步骤聚合从审计列表服务/仓储迁到运行详情仓储，列表不再返回 `steps` / `failure`
- [x] 运行详情接口在 `basic` 数据中返回 `steps`（模型/工具/出网/重试），无步骤时为全 0
- [x] 运行详情「基本信息」页签展示步骤计数
- [x] 列表移除「内部步骤」列与相关文案；失败行仍以红色标记 + 结果标签呈现
- [x] 表格单元格垂直居中；对象短 id 等宽；时间列改为日期（弱）+ 时刻（强）+ 耗时胶囊
- [x] Trace ID 下钻该 Trace 的明细视图（提示条可清除），与关联对象的运行详情区分
- [x] zh-CN / en-US 词条同步；`typecheck` 与审计 component E2E 通过

### Spec Delta

```json
[]
```

### Log

- [2026-11-17] created
- [2026-11-17T12:16:05.995Z] started tests=7ee65ed0f1cb

### Verification

- 尚未执行
- 2026-11-17T12:30:10.328Z PASS commands=2/2 executed=2 deduped=0 elapsed=22383ms tests=7ee65ed0f1cb head=3cf68d2d8c42 dirty=1
  - run-steps PASS exit=0
  - frontend-typecheck PASS exit=0
