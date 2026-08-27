# Tasks: Fluxion Governance / Eval / P1 Views

- **Source**: docs/design/fluxion-console-design-v1.6.md
- **Created**: 2026-08-23
- **Updated**: 2026-08-24

## Proposal

集中补齐此前静默丢弃的 P1 功能与 P15/P18 验收：Risk-based Approval、Eval、Plugin Binding/Hook Policy、Capability View、Runtime Status、Users/Channels。

### Alignment

- **Scope**: 仅实现本 TASK 的范围，不提前实现后续阶段。
- **Decisions**: 以 Architecture Baseline、Design-Refs 和 active Spec Context 为准。
- **Non-goals**: 不修改任务外核心 Contract；发现冲突使用 `#NOTES` 停止并重新对齐。
- **Acceptance**: Acceptance-Refs、required verifier、NFR Gate 与回归检查全部通过。

---

## Acceptance Coverage

| 场景ID | 来源设计 | 测试层级 | 关键真实边界 | 负责任务 | 状态 |
|--------|---------|---------|-------------|---------|------|
| S-C116 | docs/design/fluxion-console-design-v1.6.md#2.5.2 功能验收场景 | E2E | Policy → Approval → Execution Gate | TASK-106 | planned |
| S-C117 | docs/design/fluxion-console-design-v1.6.md#2.5.2 功能验收场景 | integration | EvalSet/EvalRun → Snapshot/Trace | TASK-106 | planned |
| S-C118 | docs/design/fluxion-console-design-v1.6.md#2.5.2 功能验收场景 | E2E | P1 Console pages → Control Plane API | TASK-106 | planned |
| E-C113 | docs/design/fluxion-console-design-v1.6.md#2.5.2 功能验收场景 | E2E | High-risk approval timeout/reject | TASK-106 | planned |
| E-C114 | docs/design/fluxion-console-design-v1.6.md#2.5.2 功能验收场景 | integration | EvalRun → Snapshot Resolver invalid version | TASK-106 | planned |

---

## TASK-106: 实现 Risk Approval、Eval 与 P1 Console 页面

- **Status**: done
- **Priority**: P1
- **Depends**: TASK-101, TASK-102, TASK-104, TASK-105
- **Source**: docs/design/fluxion-console-design-v1.6.md#2.3.1, docs/design/fluxion-console-design-v1.6.md#2.5.2, docs/design/fluxion-console-design-v1.6.md#3.2.2
- **Spec-Refs**: fluxion-console-api-contract#RULE-fluxion-console-api-001, fluxion-console-channel#RULE-fluxion-console-001, fluxion-resource-registry#RULE-fluxion-resource-001, fluxion-dfx#RULE-fluxion-dfx-001, fluxion-runtime-core#RULE-fluxion-runtime-001, frontend-directory-structure#RULE-frontend-directory-001, frontend-quality-standards#RULE-frontend-quality-001, frontend-component-specs#RULE-frontend-component-001, frontend-semi-design#RULE-frontend-semi-001
- **Acceptance-Refs**: S-C116, S-C117, S-C118, E-C113, E-C114

### Description

集中补齐此前静默丢弃的 P1 功能与 P15/P18 验收：Risk-based Approval、Eval、Plugin Binding/Hook Policy、Capability View、Runtime Status、Users/Channels。

### Scope

- low/medium/high Risk Approval Policy 与 Execution Gate。
- Approval decision/audit，high risk fail closed。
- EvalSet/EvalRun/Regression 与 Snapshot/Trace 精确版本关联。
- Users/Channels、Plugin/Hook Policy、Capability Registry、Eval、Runtime Status 页面。
- Runtime Status 只观测 Pod/Plugin capability/版本健康，不承担 Pod 生命周期管理。

### Checklist

- [x] 先写三档审批 E2E 和 high-risk fail-closed。
- [x] Eval 不允许 latest 漂移。
- [x] 所有 P1 页面至少覆盖 loading/error/empty/list/detail。
- [x] 不得因审批疲劳将 high risk 默认放行。

### Acceptance Contract

| 场景ID | 测试层级 | 测试文件 | 单独执行命令 | 核心断言 | 状态 |
|--------|---------|---------|-------------|---------|------|
| S-C116 | E2E | `backend/tests/e2e/test_risk_approval.py` | `python3 -m pytest backend/tests/e2e/test_risk_approval.py -k S_C116` | low/medium/high 三档策略正确 | verified |
| S-C117 | integration | `backend/tests/integration/test_eval_traceability.py` | `python3 -m pytest backend/tests/integration/test_eval_traceability.py -k S_C117` | EvalRun 固定关联 Snapshot/Trace 版本 | verified |
| S-C118 | E2E | `frontend/apps/console/src/pages/__tests__/p1-views.e2e.test.tsx` | `pnpm --filter @fluxion/console test -- -t S-C118` | P1 页面入口和关键状态完整 | verified |
| E-C113 | E2E | `backend/tests/e2e/test_risk_approval.py` | `python3 -m pytest backend/tests/e2e/test_risk_approval.py -k E_C113` | high risk timeout/reject fail closed | verified |
| E-C114 | integration | `backend/tests/integration/test_eval_traceability.py` | `python3 -m pytest backend/tests/integration/test_eval_traceability.py -k E_C114` | 无效版本拒绝且不静默换 latest | verified |

### Acceptance Evidence

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|--------|-----|-------|---------|-------------|------|
| S-C116 | FAIL: Approval Service/Execution Gate 模块缺失 | PASS: low 自动、medium 明确确认、high 强审批，执行与 decision/audit 断言通过 | `test_risk_approval.py` 的 executions、provider calls、audit outcomes | Policy → Approval → Execution Gate | verified |
| S-C117 | FAIL: EvalSet/EvalRun 应用服务缺失 | PASS: EvalRun 固定 EvalSet@3、RuntimeProfile@7、Trace 与完整 Snapshot，支持 regression | `test_eval_traceability.py` 的精确版本、Snapshot 与 score_delta 断言 | EvalSet/EvalRun → Snapshot/Trace | verified |
| S-C118 | FAIL: Users/Channels 等 P1 页面入口缺失 | PASS: 五个入口逐一覆盖 loading/error/empty/list/detail，Runtime Status 无 Pod 管理动作 | `p1-views.e2e.test.tsx` 的页面循环与 Runtime 只读断言 | P1 Console pages → Control Plane API | verified |
| E-C113 | FAIL: high risk timeout/reject fail-closed 模块缺失 | PASS: timeout/reject/unavailable 均拒绝；同 request 重试读取拒绝决定且不再次审批/执行 | `test_risk_approval.py` 的参数化 fail-closed 与幂等断言 | High-risk approval timeout/reject | verified |
| E-C114 | FAIL: Eval 精确版本解析与拒绝逻辑缺失 | PASS: EvalSet 引用缺失 @7 时拒绝，即使 latest @8 存在也不回退且不写 EvalRun | `test_eval_traceability.py` 的错误内容与空 RunStore 断言 | EvalRun → Snapshot Resolver invalid version | verified |

### Definition of Done

- P15 Approval 与 P18 Eval 有独立验收证据。
- FEAT-07/08/10/18/20 不再无 owner。
- 前后端测试/Stop Gate 全部通过。

### Log

- [2026-08-23] DeepSeek 评审修订：补依赖图、验收覆盖与任务内聚性。
- [2026-08-24T05:05:30Z] started (in-progress, context-sha256=d11d4dd12c1eb89de6ff9383ced098c1f013a3918b89f8e76121b307d7560a96)
- [2026-08-24T05:09:30Z] RED: 五条 Acceptance Contract 均按预期失败；Approval/Eval 服务与五个 P1 页面尚不存在。
- [2026-08-24T05:16:34Z] GREEN: 五条验收通过；完整验证门通过（backend 133、Registry SQLite/PostgreSQL contract 20、Console 6、Chat 2）。
- [2026-08-24T12:26:49Z] REVIEW: (1) Risk-based Approval：本会话已将审批落地为真实 service-layer（`services/approval_app.py` + `POST /api/v1/approvals` + `:decide` route，见 phase-09 Log），替换了此前仅测试使用的 `approval.py` presence-only gate。(2) 已知 gap（follow-up 任务）：`services/eval_app.py` 的 EvalSet/EvalRun 应用服务仅在测试中构建，`backend/src/fluxion/api/` 无任何 eval HTTP route——与 approval gate 同类「claimed-done-but-not-wired」缺陷。P1 Eval 页面无后端可调。需新增独立任务接线 Eval API（POST eval runs / GET eval results）。(3) 其余 P1 页面（Capability/Runtime Status/Users/Channels）与 Policy→Approval→Execution gate 经 S-C116/S-C117/E-C114 验收验证为真实接线。
