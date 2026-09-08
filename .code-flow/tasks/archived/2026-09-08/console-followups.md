# Tasks: console-followups

- **Source**: `.code-flow/tasks/archived/2026-09-08/console-redesign/console-redesign.md#Follow-ups`
- **Created**: 2026-09-08
- **Updated**: 2026-09-08

## Proposal

收敛 console-redesign 归档时 F1~F4 四个缺口：执行耗时/智能体名/失败原因（run_payload 加字段）、用户链接 Tab（新 endpoint）、trace 跳转（keyword 匹配 trace_id + 测试面板跳转）、顶栏搜索/头像、hover 快捷、编辑器折叠持久化。后端只加字段与只读查询，不做迁移（expires_at 需 ADR，不做）。

### Alignment

- **Scope**: 纳入 F1~F4；排除 expires_at（需 ADR + 迁移）、已撤销历史（list 硬过滤 revoked）。
- **Decisions**:
  - run_payload 加 `latency_ms`/`error`/`agent_definition{id,version}`；agent_name 前端 getResource join（既有模式）。
  - keyword 扩展匹配 trace_id（顺带修“搜索执行 ID / Trace”误导占位）。
  - 新 endpoint `GET /api/v1/platform-users/{id}/chat-access`（只读，无审计）。
  - F3 最小化：搜索跳执行/审计（URL keyword 透传）+ 静态 admin 头像（无登出，后端无 auth）。

---

## FU-01: hover 快捷 + 编辑器折叠持久化（纯前端）

- **Status**: done
- **Priority**: P1
- **Depends**:

### Description

StandardListShell 行操作 hover 露出（CSS）；编辑器左导航折叠态 localStorage 持久化。

### Checklist

- [x] `.row-actions` 默认半透明，行 hover 全显（保留键盘/触屏可达：focus-within 同样全显）
- [x] 编辑器 Nav openKeys 受控 + 持久化
- [x] vitest 断言（console-followups.test.tsx FU-01）

### Evidence

- FU-01 GREEN；全量 156+5 通过（见 FU-04）。

### Log

- [2026-09-08] created (draft)
- [2026-09-08] completed (done)

---

## FU-02: 执行耗时/智能体名/失败原因（前后端）

- **Status**: done
- **Priority**: P0
- **Depends**:

### Description

后端 `run_payload` 新增 `latency_ms`、`error`（截断 500）、`agent_definition{id,version}`；keyword 扩展 OR 匹配 trace_id；前端类型/parser/inMemory 跟进，执行列落地（耗时格式化、失败摘要真实文案、智能体名 join），修正搜索占位诚实性。

### Checklist

- [x] `console_payloads.run_payload` 加三字段（先写后端测试 RED：KeyError latency_ms）
- [x] `trace_store.list_recent` + `tracing._filter` keyword 匹配 trace_id（双实现同形 + 测试；附带修 docstring）
- [x] 前端 types/parsers/inMemory/列渲染（耗时 ms→s 格式化；agent 名 getResource join 回退 id）
- [x] 后端 pytest（6/6） + 前端 vitest 全过
- [x] 真机验证（执行列/搜索框；失败 error 行待一条真实失败执行出现——dev traces 走进程内存，重启即失，见备注）

### Evidence

- `backend/tests/api/test_console_runs_followups.py` 4 passed（含 PG 真库 keyword）。
- 备注：dev bundle trace 用 InMemoryTraceStore，2 条演示执行因后端重启已失（PG 无损）；dev 持久化 trace 另议。

### Log

- [2026-09-08] created (draft)
- [2026-09-08] completed (done)

---

## FU-03: 用户链接 Tab + 测试面板 trace 跳转（前后端）

- **Status**: done
- **Priority**: P0
- **Depends**:

### Description

新只读 endpoint `GET /api/v1/platform-users/{id}/chat-access`（透传 list_chat_access）；前端 ConsoleApi/parsers/inMemory 跟进；User360 新增链接 Tab（agent 名 join + 复制/撤销 RiskConfirm）；测试面板 traceId 跳转执行记录（URL keyword，依赖 FU-02 keyword 扩展）；RunsPage/AuditPage 支持 URL keyword 初始化（兼 F3 搜索落点）。

### Checklist

- [x] 后端路由 + service + 测试 RED/GREEN（含 404 未知用户；租户隔离走 actor；token/token_hash 不回显；撤销后不可见）
- [x] 前端 API/类型/parser/inMemory + User360 链接 Tab + 撤销
- [x] 测试面板「跳转执行记录」按钮 + RunsPage/AuditPage URL keyword 初始化
- [x] vitest + 后端 pytest 全过 + 真机验证（alice 4 链接 Tab `/tmp/console-fu-links3.png`）

### Evidence

- `backend/tests/api/test_console_user_chat_access.py` 2 passed；前端 FU-03 两用例 GREEN。
- 备注：token 仅签发时可见，Tab 内不可复制（后端不存明文）；in-memory completed 补 trace_id（dev 假数据）。

### Log

- [2026-09-08] created (draft)
- [2026-09-08] completed (done)

---

## FU-04: 顶栏全局搜索 + 头像（前端）

- **Status**: done
- **Priority**: P2
- **Depends**: FU-03（复用 URL keyword 落点）

### Description

顶栏搜索框：范围（执行/审计）+ 输入回车跳转（keyword 预填）；静态 admin 头像 + tooltip（无登出，后端无 auth，不做假登出按钮）。

### Checklist

- [x] 搜索框 + 跳转 + 落点 keyword 生效断言（console-followups.test.tsx FU-04）
- [x] 头像 + 真机截图（顶栏 A 头像 + 本地环境徽，见各截图右上）

### Log

- [2026-09-08] created (draft)
- [2026-09-08] completed (done)

---

## FU-05: dev bundle trace 统一走 PG（持久化，重启不丢）

- **Status**: done
- **Priority**: P0
- **Depends**:

### Description

dev_bundle 改用 `PostgresTraceStore`（runtime 写入 + console 读取同一 PG store，与生产同形态）；lifespan 初始化 trace_records 表。

### Checklist

- [x] dev_bundle 装配 PG trace store（runtime + console 同实例）
- [x] RED→GREEN：`test_dev_trace_pg_persistence.py`（bundle 重启前后 GET /runs 均可读 + latency_ms）
- [x] ruff + mypy + 相关套件（dev_bundle/channel/agent_test_run 17 passed）

### Evidence

- 根因：dev 经 `runtime.trace_store` 默认 `InMemoryTraceStore`，后端 reload 即失；PG trace_records 一直为空。
- 附带发现：dev 模式中间件 pin 租户（忽略 X-Tenant-ID header），测试 seed 须用 dev 租户。

### Log

- [2026-09-08] created (draft)
- [2026-09-08] completed (done)
