# ADR-A017 Agent Command Plane 契约（Snapshot Directive / ExecutionState / Session Head）

**引用**：`fluxion-agent-command-plane-design.md`（§10/§11/§14）、规则 25。

**背景**（2026-09-08 核实，`main@33d167b`）：

- `resources/contracts.py:175-221` 的 `ExecutionSnapshot` 无显式意图字段，无法表达"本轮用户指定用某 Skill"；
- `services/runtime_contracts.py:138-144` 只有 `ExecutionTerminalState` 终态视图（finalize 用），无 durable 执行状态机、无可寻址 active execution；
- `services/channel_app.py:148,179,252` 把 external `conversation_id` 直传为 Runtime `session_id`，IM 通道无法支持 `/new`。

**决策**：

1. **Snapshot 显式意图**：`ExecutionSnapshot` 新增 `invocation_directive: ResolvedInvocationDirective | None`（`kind` / `capability_id` / `exact version`）。`canonical_digest` 对全模型 dump 哈希（仅排除 `created_at`/`execution_id`/`trace_id`，见 `resources/snapshot_digest.py:15-16`），新字段自动参与 digest；`None` 按现有规范形式参与序列化——digest 天然随模型演进，**不承诺跨版本可比**（符合"面向当前最优实现，不保留兼容层"原则）。
2. **显式激活语义**：有 `skill` directive 时，仅选中 Skill 的 instruction 进入 `active_skill_instruction`，其余 Skill 不作为本轮激活注入；无 directive 时 `skill_instructions` 全量注入不变。Tool 权限始终源自完整 effective 图，selected Skill 不扩权（RULE-04）。
3. **Durable 执行状态机**：新增 `ExecutionState`（`created/running/cancelling/completed/failed/cancelled/timed_out`）。`CREATED→RUNNING→COMPLETED/FAILED/TIMED_OUT`；`RUNNING→CANCELLING→CANCELLED`；终态不可出，首次终态获胜。`ExecutionTerminalState` 保留为 finalize 终态视图，不替代。
4. **`runtime_executions` 表**：主键 `execution_id`，含租户/用户/Agent/Session 四元组 + `state` + `owner_instance_id` + `requested_skill_id` + CAS `revision`。Partial unique index `(tenant_id, platform_user_id, agent_id, session_id) WHERE state IN ('created','running','cancelling')`——一个 Session 至多一个 Active Execution。
5. **Session Head**：新增 `chat_session_heads`，主键 `(tenant_id, channel_type, external_conversation_id, platform_user_id, agent_id)`，`active_session_id` 服务端生成（`sess_<32hex>`）。Runtime `session_id` 不再直接等于 external conversation id。`/new` 只轮换指针，不删 UserProfile/Personal Memory/授权/Trace（旧 Memory 行保留、仅不再被引用）。
6. **取消寻址**：`/stop` 只接受当前 principal 四元组寻址，不接受任意 `execution_id`；语义为"阻止继续推进、尽最大可能中断当前调用"，不承诺回滚已完成副作用。

**后果**：TASK-004（Skill）/ TASK-005·006（Execution Control）/ TASK-003（Session）按本契约实现； digest 含 directive 后旧快照 digest 不可比属预期（原则：面向当前最优实现，不保留兼容层）。
