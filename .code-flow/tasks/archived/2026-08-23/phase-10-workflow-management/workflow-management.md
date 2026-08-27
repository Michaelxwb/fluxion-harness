# Tasks: Fluxion Workflow Definition 管理

- **Source**: docs/design/fluxion-console-design-v1.6.md
- **Created**: 2026-08-23
- **Updated**: 2026-08-24

## Proposal

把 WorkflowDefinition 从收尾大任务中拆出，形成独立前后端垂直切片；V1 UI 可使用 JSON/YAML+Form，但 Validate/Publish/Version 必须完整。

### Alignment

- **Scope**: 仅实现本 TASK 的范围，不提前实现后续阶段。
- **Decisions**: 以 Architecture Baseline、Design-Refs 和 active Spec Context 为准。
- **Non-goals**: 不修改任务外核心 Contract；发现冲突使用 `#NOTES` 停止并重新对齐。
- **Acceptance**: Acceptance-Refs、required verifier、NFR Gate 与回归检查全部通过。

---

## Acceptance Coverage

| 场景ID | 来源设计 | 测试层级 | 关键真实边界 | 负责任务 | 状态 |
|--------|---------|---------|-------------|---------|------|
| S-C108 | docs/design/fluxion-console-design-v1.6.md#2.5.2 功能验收场景 | E2E | Workflow UI/API → Validator → Registry | TASK-105 | planned |
| E-C104 | docs/design/fluxion-console-design-v1.6.md#2.5.2 功能验收场景 | integration | Workflow Validator → Capability Registry | TASK-105 | planned |

---

## TASK-105: 实现 WorkflowDefinition Validate/Publish 与 Console 管理页

- **Status**: done
- **Priority**: P2（业务接入层）
- **Depends**: TASK-101, TASK-102, TASK-104
- **Source**: docs/design/fluxion-console-design-v1.6.md#3.2.10, docs/design/fluxion-console-design-v1.6.md#2.5.2
- **Spec-Refs**: fluxion-console-api-contract#RULE-fluxion-console-api-001, fluxion-console-channel#RULE-fluxion-console-001, fluxion-resource-registry#RULE-fluxion-resource-001, fluxion-dfx#RULE-fluxion-dfx-001, fluxion-workflow-capability#RULE-fluxion-workflow-001, frontend-directory-structure#RULE-frontend-directory-001, frontend-quality-standards#RULE-frontend-quality-001, frontend-component-specs#RULE-frontend-component-001, frontend-semi-design#RULE-frontend-semi-001
- **Acceptance-Refs**: S-C108, E-C104

### Description

把 WorkflowDefinition 从收尾大任务中拆出，形成独立前后端垂直切片；V1 UI 可使用 JSON/YAML+Form，但 Validate/Publish/Version 必须完整。

> **业务接入层说明**：Workflow Engine/DSL 执行与业务 WorkflowDefinition 归业务接入层，不在开源 V1 范围（见 Architecture Baseline §12）。本任务不阻塞 V1 发布；Workflow Tool Adapter 接入协议由 TASK-004 在 V1 实现（FEAT-13/S-R08）。当前任务仅作为 WorkflowDefinition 资源管理的设计留存。

### Scope

- WorkflowDefinition DSL/Schema/Capability ref validator。
- Draft/Validate/Publish/Version API。
- Semi Design Workflow 管理页面。
- 与 Runtime Workflow Adapter Contract 对齐。

### Checklist

- [x] 先写有效/无效 Workflow 验收。
- [x] 错误 DSL/Capability ref 必须阻止 Publish。
- [x] 不在 Console 实现 Workflow Engine durable state。

### Acceptance Contract

| 场景ID | 测试层级 | 测试文件 | 单独执行命令 | 核心断言 | 状态 |
|--------|---------|---------|-------------|---------|------|
| S-C108 | E2E | `frontend/apps/console/src/pages/workflows/__tests__/workflow-publish.e2e.test.tsx` | `pnpm --filter @fluxion/console test -- -t S-C108` | Validate 后发布并产生版本 | verified |
| E-C104 | integration | `backend/tests/integration/test_workflow_validation.py` | `python3 -m pytest backend/tests/integration/test_workflow_validation.py -k E_C104` | 错误 DSL/Capability ref 阻止发布 | verified |

### Acceptance Evidence

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|--------|-----|-------|---------|-------------|------|
| S-C108 | FAIL: Console 无 Workflows 页面/导航，无法进入 Validate/Publish 流程 | PASS: Semi Workflows 页面完成 Draft→Save→Validate→Publish，并在 Version 列表产生 v2 | `workflow-publish.e2e.test.tsx` 的 Published v2、latest 与 listVersions 断言 | Workflow UI/API → Validator → Registry | verified |
| E-C104 | FAIL: Workflow Validate API 返回 405，错误 DSL/Capability ref 未在发布前校验 | PASS: 错误 DSL/缺失 Capability 均返回 400，两个资源保持 Draft；有效 Workflow 可发布 | `test_workflow_validation.py` 的状态码、错误信息与 Registry 状态断言 | Workflow Validator → Capability Registry | verified |

### Definition of Done

- WorkflowDefinition 前后端垂直切片完成。
- Runtime Adapter Contract 不漂移。
- 测试/Stop Gate 全部通过。

### Log

- [2026-08-23] DeepSeek 评审修订：补依赖图、验收覆盖与任务内聚性。
- [2026-08-24T04:41:46Z] started (in-progress, context-sha256=e95f630203689216b8a4840fb3d7a7172ed05f7af6204a36d4a43d659fcb5eb6)
- [2026-08-24T04:48:05Z] RED: S-C108 因 Workflows 页面缺失失败；E-C104 因 Validate API 缺失且 Publish 未执行 DSL/Capability ref 校验失败。
- [2026-08-24T05:00:18Z] GREEN: S-C108/E-C104 通过；完整验证门通过（backend 127、Registry SQLite/PostgreSQL contract 20、Console 5、Chat 2）。
- [2026-08-24T12:26:49Z] REVIEW+FIX: (1) HIGH：`:validate` 对无效 workflow 返回 200 + valid:false，未达 E-C104 要求的 400（`validate_workflow_version` 死代码未接线）。已修复：`validate_resource_version` 对 WORKFLOW 校验失败时 raise（400 + 具体诊断）；(2) HIGH：`validate`/`publish` 只校验 WORKFLOW，MCP/Skill 等无效 spec 可通过（S_P13_05 contract 破坏）。已修复：新增 `_definition_model/_validate_definition` kind→model dispatch，非 workflow 在 `:validate` 返回 valid:false（200），在 `:publish` 校验失败 raise 400；`console_helpers.mcp_spec` 修正为真实 MCP contract（stdio 需 command，server_uri 由 runtime 自建）。(3) DoS：workflow 无 size/depth 上限，`_validate_capabilities` N+1。已修复：`WorkflowDefinition` 增加 steps max=200、字段长度上限，`_find_plaintext_secret` 增加深度限制（100）。(4) 深嵌套 spec 触发 RecursionError→500。已修复：深度受限遍历 + ValueError。(5) workflow 多错误只报第一条。已修复：`_format_schema_error` 汇总前 5 条。已知 gap（记录）：capability parameter 交叉校验无 schema 可对（parameters 为自由 dict），发布成功后在执行期失败；`_check_expected_base` 跨实例乐观锁竞态需 DB 级串行化。
