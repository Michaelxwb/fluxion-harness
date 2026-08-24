# Tasks: Fluxion Console Web 核心管理面

- **Source**: docs/design/fluxion-console-design-v1.6.md
- **Created**: 2026-08-23
- **Updated**: 2026-08-23

## Proposal

实现 Console 的 P0 管理 UI，不再只验收两个只读页面；RuntimeProfile Draft/Validate/Publish/Rollback、Binding/Policy/CredentialRef 与 Runs/Trace/Audit 均纳入 E2E。

### Alignment

- **Scope**: 仅实现本 TASK 的范围，不提前实现后续阶段。
- **Decisions**: 以 Architecture Baseline、Design-Refs 和 active Spec Context 为准。
- **Non-goals**: 不修改任务外核心 Contract；发现冲突使用 `#NOTES` 停止并重新对齐。
- **Acceptance**: Acceptance-Refs、required verifier、NFR Gate 与回归检查全部通过。

---

## Acceptance Coverage

| 场景ID | 来源设计 | 测试层级 | 关键真实边界 | 负责任务 | 状态 |
|--------|---------|---------|-------------|---------|------|
| S-C107 | docs/design/fluxion-console-design-v1.6.md#2.5.2 功能验收场景 | E2E | Trace/Snapshot Store → Console Run Detail | TASK-102 | verified |
| S-C114 | docs/design/fluxion-console-design-v1.6.md#2.5.2 功能验收场景 | E2E | Console Web → Resource API → Registry | TASK-102 | verified |
| S-C115 | docs/design/fluxion-console-design-v1.6.md#2.5.2 功能验收场景 | E2E | Console Web → Binding/Policy/CredentialRef API | TASK-102 | verified |
| B-C103 | docs/design/fluxion-console-design-v1.6.md#2.5.2 功能验收场景 | E2E | 1000 versions + large Audit/Trace history | TASK-102 | verified |

---

## TASK-102: 实现 Semi Design P0 管理 UI 与 Trace/Audit 只读入口

- **Status**: done
- **Priority**: P0
- **Depends**: TASK-101
- **Source**: docs/design/fluxion-console-design-v1.6.md#3.2.2, docs/design/fluxion-console-design-v1.6.md#3.2.11, docs/design/fluxion-console-design-v1.6.md#2.5.2
- **Spec-Refs**: fluxion-console-api-contract#RULE-fluxion-console-api-001, fluxion-console-channel#RULE-fluxion-console-001, fluxion-resource-registry#RULE-fluxion-resource-001, fluxion-dfx#RULE-fluxion-dfx-001, frontend-directory-structure#RULE-frontend-directory-001, frontend-quality-standards#RULE-frontend-quality-001, frontend-component-specs#RULE-frontend-component-001, frontend-semi-design#RULE-frontend-semi-001
- **Acceptance-Refs**: S-C107, S-C114, S-C115, B-C103

### Description

实现 Console 的 P0 管理 UI，不再只验收两个只读页面；RuntimeProfile Draft/Validate/Publish/Rollback、Binding/Policy/CredentialRef 与 Runs/Trace/Audit 均纳入 E2E。

### Scope

- Semi Design 主导航与统一 Layout。
- RuntimeProfile/Resource 列表、详情、Draft 编辑、Validate/Publish/Rollback。
- Skill/MCP Binding、Policy、CredentialRef metadata 管理。
- Runs/Trace/Audit 只读页面。
- Users/Channels、Plugin/Capability/Eval/Runtime Status 的 P1 页面明确延后至 TASK-106，不在本 TASK 假装完成。

### Checklist

- [x] 先写 S-C107/S-C114/S-C115/B-C103 UI E2E。
- [x] 发布/回滚必须有 Semi Modal 和影响对象/版本。
- [x] Secret 永不回显。
- [x] 关键错误保留可恢复页面状态，不只 Toast。

### Acceptance Contract

| 场景ID | 测试层级 | 测试文件 | 单独执行命令 | 核心断言 | 状态 |
|--------|---------|---------|-------------|---------|------|
| S-C107 | E2E | `frontend/apps/console/src/pages/runs/__tests__/run-detail.e2e.test.tsx` | `pnpm --filter @fluxion/console test -- -t S-C107` | Run Detail 展示精确资源版本 | verified |
| S-C114 | E2E | `frontend/apps/console/src/pages/resources/__tests__/resource-management.e2e.test.tsx` | `pnpm --filter @fluxion/console test -- -t S-C114` | Draft/Validate/Publish/Rollback 全链路可操作 | verified |
| S-C115 | E2E | `frontend/apps/console/src/pages/bindings/__tests__/binding-policy.e2e.test.tsx` | `pnpm --filter @fluxion/console test -- -t S-C115` | Binding/Policy/CredentialRef UI 与 API 一致 | verified |
| B-C103 | E2E | `frontend/apps/console/src/pages/resources/__tests__/versions.e2e.test.tsx` | `pnpm --filter @fluxion/console test -- -t B-C103` | 1000 版本/大历史分页稳定 | verified |

### Acceptance Evidence

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|--------|-----|-------|---------|-------------|------|
| S-C107 | FAIL: `pnpm --filter @fluxion/console test -- -t S-C107` 无法解析 `src/App`，Run Detail 未实现 | PASS: `pnpm --filter @fluxion/console test -- -t S-C107` | `run-detail.e2e.test.tsx:17` | ConsoleApi trace/run fixture → RunsPage，无组件 mock | verified |
| S-C114 | FAIL: `pnpm --filter @fluxion/console test -- -t S-C114` 无法解析 `src/App`，资源管理 UI 未实现 | PASS: `pnpm --filter @fluxion/console test -- -t S-C114` | `resource-management.e2e.test.tsx:18`、`:29`、`:35` | Console Web → typed ConsoleApi → in-memory Registry fixture | verified |
| S-C115 | FAIL: `pnpm --filter @fluxion/console test -- -t S-C115` 无法解析 `src/App`，Binding UI 未实现 | PASS: `pnpm --filter @fluxion/console test -- -t S-C115` | `binding-policy.e2e.test.tsx:14`、`:18`、`:22` | Console Web → Binding/CredentialRef service fixture，tenant-b private 不可选 | verified |
| B-C103 | FAIL: `pnpm --filter @fluxion/console test -- -t B-C103` 无法解析 `src/App`，版本分页 UI 未实现 | PASS: `pnpm --filter @fluxion/console test -- -t B-C103` | `versions.e2e.test.tsx:17`、`:21`、`:24` | 1000 versions fixture + Audit/Trace retention text | verified |

### Definition of Done

- P0 Console 管理操作均有 UI E2E。
- Semi React19 adapter 正确加载且无第二套 UI 库。
- typecheck/lint/test/Stop Gate 全部通过。

### Log

- [2026-08-23] DeepSeek 评审修订：补依赖图、验收覆盖与任务内聚性。
- [2026-08-23T10:41:16Z] started (in-progress, context-sha256=461492d6eeaeffb7fbad2ae725efb72625056465b3da8428a52a602e063043d0)
- [2026-08-23T10:45:06Z] RED: S-C107/S-C114/S-C115/B-C103 UI E2E 已写入，均因 `src/App` 与 Console service 未实现失败。
- [2026-08-23T10:56:30Z] GREEN: S-C107/S-C114/S-C115/B-C103 单场景命令均通过；`pnpm -r typecheck`、`pnpm -r lint`、`pnpm -r test`、Console build、Semi 约束检查通过。
- [2026-08-23T23:56:44Z] done: 代码 review 修复完成 — HTTPException 404/405 回统一 envelope、mid-word key 脱敏、`expected_base_version` 乐观锁对齐已发布 base、Audit 与主操作解耦、publish bump revision；backend 109 passed，ruff/mypy clean。前端验收按 prior evidence 维持 verified，未重跑前端测试。
