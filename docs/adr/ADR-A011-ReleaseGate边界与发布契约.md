# ADR-A011 ReleaseGate applies_to 边界与前后端发布契约

**引用**：ADR-A008（Eval 关联）、REQ-EVAL-001、ADR-A003（Snapshot 关联）、规则 22/24、规则 25。

**背景**：

ReleaseGate 当前挂在通用 Resource Publish 管道上，对**所有** ResourceKind 无差别生效（`services/console_resource_lifecycle.py:283-295`），无 kind 白名单；生产装配 `release_gate_enforced=True`（`api/production_bundle.py:185`），而 Console 前端 `publishVersion()` 只发送空 body `{}` 且整个前端无 gate 传参能力（`httpConsoleApi.ts:160-166`）——生产环境下前端发起的任何发布都会被 `RELEASE_GATE_BLOCKED(38001)` fail-closed 阻断（`tests/integration/test_release_gate.py:170-206` 实证 RUNTIME_PROFILE 同样被阻断）。同时前端不发送 `expected_base_version`，乐观并发检查被静默跳过。

**候选**：

1. 所有 kind 都要求 gate（把 eval 门推广为全资源发布门）；
2. `applies_to(kind)` 白名单：AGENT_DEFINITION 必选、WORKFLOW 本轮不纳入、其余 kind 默认不评估；
3. 废除 ReleaseGate（发布无质量门）。

**决策**：选 2（eval 度量对连接/技能/凭据类资源无意义；全量门只会迫使生产关掉 enforced；废除门违背 REQ-EVAL-001 的版本关联质量要求）。

边界定型：

```text
ReleaseGatePolicy.applies_to(kind):
  AGENT_DEFINITION     ✅ enforced 模式下发布必须携带 gate 参数
  WORKFLOW             ❌（V1 不纳入，见重新评估条件）
  MODEL_DEFINITION     ❌
  MODEL_PROVIDER       ❌
  SKILL / TOOL / MCP_SERVER / RUNTIME_PROFILE / SECRET / POLICY / EVAL_SET ❌
```

发布契约统一：

- **保存（working draft）**：基础校验——typed spec 校验 + 引用存在性；不评估 gate；
- **发布（publish）**：完整校验（validate-publish）+ 若 `applies_to(kind)` 则评估 gate；enforced 模式下 AGENT_DEFINITION 无 gate → 409 fail-closed 保持；
- **前后端契约**：`PublishPayload{publish_note?, expected_base_version?, gate?}` 三字段可选、`extra=forbid`；前端 `publishVersion` 必须具备传递全部三字段的能力，Agent Editor 发布默认携带 `expected_base_version`（乐观并发），禁止「FE 永远发空 body、BE 却按 enforced 阻断」的双端不一致。

**代价**：

- 非 Agent 资源发布不再有 eval 门（接受：这些资源的正确性由 typed spec 校验与引用完整性保证）；
- 非门类资源在 enforced 装配下发布放行，`test_release_gate.py` 需改写断言（RUNTIME_PROFILE 不再 409）。

**失败模式**：

- AGENT_DEFINITION 发布无 gate 且 enforced → 409 `RELEASE_GATE_BLOCKED`，资源保持 draft（现状保持）；
- `expected_base_version` 与 Registry 当前 published base 不符 → 乐观并发冲突拒绝，管理员刷新后重试；
- gate 参数指向不存在的 EvalRun → 发布拒绝，错误信息带 EvalRun 定位。

**验收**：

- enforced 装配下非门 kind（含 RUNTIME_PROFILE）空 body 发布成功（B-S-04）；
- AGENT_DEFINITION enforced 无 gate → 409；带合法 gate → 发布成功（B-S-05）；
- 前端 `publishVersion` 类型签名与调用链支持三字段（B-S-05）。

**重新评估条件**：

- Workflow eval 度量（基于 ExecutionSnapshot 的 baseline/candidate 比较，ADR-A003 amend）建立并稳定运行后，重评估 WORKFLOW 纳入 `applies_to`；
- 其余 kind 若出现独立质量门需求（如 Skill 包安全扫描），按 kind 增量扩展，不再回到全量门。
