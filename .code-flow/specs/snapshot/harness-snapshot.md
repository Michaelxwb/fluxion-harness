---
{
  "description": "Run/Task 快照冻结",
  "next_rule": 11
}
---

# harness-snapshot

## Rules

- [RULE-snapshot-001] 每个新 Run/Task 在执行前冻结其执行契约要求的完整非密钥材料，包括 Agent/Model、模型池与摘要模型、Prompt 模板、执行策略、工具定义及依赖内 Skill/MCP 材料；Task 快照按执行模式约束依赖集合，不能用固定单 Skill 假设覆盖所有模式，也不能以 hash 替代恢复所需内容。任务预算归 execution snapshot，Run 的等价策略载体保持其自身契约。定时任务冻结实际只读记忆块及版本，等待与恢复使用原块；读取失败显式记录降级。快照大小按设置所有者约束，超限拒绝而非截断；快照、检查点随执行保留，配置变化只影响后续新执行，非终态与终态写入均受状态及执行权 CAS 保护。

- [RULE-001] 共享 Agent 执行内核仅依赖中立执行契约和端口，既有 Agent 循环由不同宿主适配，不得复制简化循环或按宿主类型分支。持久检查点必须覆盖完整模型回合与异步工具挂起所需的调用、结果消费和预算状态；恢复同一执行不能重新解析当前定义。上下文压缩失败必须保留原历史，批量工具结果外置失败整轮回滚为内联结果，并记录可观测的降级，不能使主执行失败。

- [RULE-002] Run 的 policy_json 冻结 locale.default_locale；旧行缺失时不追加语言段，失败文案回落默认语言。Draft 创建时读取当前 locale.default_timezone 属于 authoring 输入，不替代执行期冻结；Worker AGENTIC 在 admission 把用户 memory 的 language 偏好冻结进 SnapshotMemory.block.language，执行期与平台默认经同一 resolve_reply_locale 裁决，配置变化只影响后续新执行。

- [RULE-003] 已完成工具调用以持久化的规范结果作为恢复事实。实时与恢复使用同一进展判定，并按调用与消费水位去重。已准备但没有可核实完成或未派发事实的调用视为结果未知。重试门禁基于冻结安全性、受信执行阶段和幂等身份，恢复及同一会话后续运行不能靠变参或换能力入口重做未核实的外部操作。

- [RULE-004] 上下文压缩以 Run/Task 创建时冻结的 history_budget_tokens 作为唯一历史预算；装配只读且按预算分页读取，只有请求边界做一次保头保尾裁剪，任何未进入请求的历史必须带可见省略标记与审计。摘要只处理从 covered_seq 起的连续材料区间，替换只移除该材料区间并保留头部、独立尾部与 pinned；coverage 只推进到实际材料末条，不覆盖当前输入。冻结压缩配置严格解析，旧键、旧 policy、旧摘要和旧平台设置不保留兼容解析。

- [RULE-005] 工具完成事件记录失败控制事实；检查点保留失败状态、待完成操作和调用去重所需事实。实时执行与检查点恢复使用同一控制判定，不能通过重解析当前工具定义或更换入口重做未知外部操作。

- [RULE-006] 执行边界异常必须以通用事实归一化：边界来源、停止是否确认、发送/结果事实和证据完整性共同决定 CONTINUE/WAIT/STOP。底层错误码只作为诊断来源，不得让不同适配器复制不同恢复流程；停止未确认或执行权/关键事实不可保证时不得继续派发可能产生副作用的调用。

- [RULE-007] 重放和同会话后续执行须沿用与实时派发一致的外部操作身份及重试判定。规范事件必须区分派发前拒绝、结果已知和待核实副作用。受信操作身份缺失时，以冻结授权资源判断潜在重叠；只有可关联的受信事实可以解除待核实状态。

- [RULE-008] 同会话续行应基于可核验的用户目标、已确认条件和已完成执行事实。历史省略标记应对应实际保留的当前输入，续行不可把未知操作或未核实结果当作已完成。

- [RULE-009] Run/Task 工具事件使用版本化 v2 契约：assistant turn 同时保存模型名与内部名；外部执行前写 prepared/STARTED 事实（args_hash、脱敏预览、来源/技能/工具/retry_safety）；完成事件保存 outcome、artifacts role 与 follow-ups；恢复身份只用内部名。

- [RULE-010] SnapshotSkill 必须冻结正式 key 字段（来自 ResolvedSkill.key），catalog/提示词/执行路由/子 Task 快照统一使用；不得从 frontmatter.key 推断。

## Conventions

- **恢复期间使用已持久化的工具事实**（RULE-003）：✅ 待核实的外部操作保留原身份和重试限制；❌ 通过更换工具入口或参数重做效果仍未知的操作。
- **按边界事实决定是否继续**：✅ 停止已确认时，可将截断输出标为部分证据交给 Agent；❌ 停止未确认或执行权丢失时继续派发可能产生副作用的调用。

- **后台 Skill 的 CLI 参数随执行快照冻结**：独立的 `argv` 与 JSON stdin 分开存储，纳入快照 hash 和 operation 输入指纹；Worker 重试只读取原快照并校验参数，省略表示没有 CLI 参数。✅ 同一 call 改参数返回幂等冲突，重试原样执行；❌ 从当前 Skill 说明重建参数、将控制字段混入业务 input、不同参数复用旧操作。

- **异步容量与绝对 deadline 在 Run 创建时冻结**：policy_json.async_tools 由严格 AsyncToolPolicy 与 Agent 覆盖构建；deadline_at 从首次 start_time 与有效 AgentPolicy 计算。Runtime `async_tools/operations.py` 在 Conversation→Run 锁序内核对冻结能力、按真实 call 去重并预留容量，再同事务写 operation + SUBMIT outbox；取消、失败和 Reaper 同事务写 JOIN/未受理提交的取消意图。✅ 检查点恢复读取原 policy/deadline；❌ 在提交或恢复时重新取设置、延期 deadline，或在 HTTP 中持有业务行锁。

- **快照 hash 口径统一为 `"sha256:"` + canonical JSON**：`json.dumps(sort_keys=True, separators=(",", ":"), ensure_ascii=False)` 后取 SHA256。contracts 侧 `snapshot_hash` 与 Runtime 侧 `_snapshot_hash` 必须同口径，否则跨服务比对不等（`packages/contracts/src/muad_contracts/snapshot.py`；`apps/agent-runtime/src/muad_agent_runtime/application/run_service.py` 的 `_canonical_json` / `_snapshot_hash`）。✅ 两侧都复用同一组 `sort_keys`/紧凑分隔符/`ensure_ascii=False`；❌ 任一侧漏掉其中一项（例如漏 `ensure_ascii=False` 导致中文被转义）→ hash 不等。
- **立即 Skill 快照的 `skills` 数组恰好 1；Schedule v4 按模式 0..N**：REMINDER 0、SKILL 1、AGENTIC 冻结 allowlist 涉及的全部 Skill；执行器按 `skill_artifact_id` 精确匹配，不能取第一项。`skills` 为空是合法模式（REMINDER），不能因此拒绝执行。
- **Snapshot v4 冻结依赖范围内的完整非密材料**：Agent instructions/runtime_config、Model 非密定义 + `pool_members`/`summary_model`、Policy、allowlist 工具完整 `input_schema`、MCP endpoint/非密 headers/`secret_header`、Memory 实际 READ_ONLY 注入块与 revision、Prompt 模板、Grants；不冻结 Skill 代码字节/整库 catalog，`api_key`/`auth_secret` 不进快照。
- **Snapshot 大小超限拒绝**：超过平台设置上限返回 `SNAPSHOT_TOO_LARGE`，不截断；快照随 Execution 保留期释放。
- **`api_key` 必须从快照与 hash 中剥离**：`_snapshot_model` 把 `model_json` 的 `api_key` 弹掉，认证实时走 API-09 读取（`apps/agent-runtime/src/muad_agent_runtime/application/run_service.py` 的 `_snapshot_model`；断言 `tests/agent_runtime/test_snapshot_freeze.py` 的 hash 与 api_key 用例）。这是 snapshot 与 secret 两个 spec 的接缝（密钥不进快照由 RULE-secret-001 拥有）：✅ 快照只留 `base_url`/`model_id` 等非密钥字段；❌ 密钥进快照或进 hash，会造成密钥轮换即 hash 漂移且密钥落盘。
- **非终态写入同样是 CAS**：真实入站 resume 在 Conversation→Run→Interrupt 锁序内，用 `status == WAITING_INPUT` 与绝对 deadline 作条件置为 `CREATED`；PG claim 再获得执行 epoch/lease。输入、确认效果、interrupt消费及 submission 与该 CAS 同事务。✅ `UPDATE ... WHERE id = ? AND status = <预期>`；❌ 先读后写、或只按 id 更新。
- **旧 Run resume 不重新 resolve 定义或设置**：从原快照重建 Agent/Model/Skill/MCP，凭据走实时 API-09。`test_snapshot_freeze.py` 实测定义单腿变化与旧行逐列不变；`test_memory_redesign_snapshot.py` 实测设置单叶变化、新Run工具历史为0、旧resume仍保留原6轮。✅ 原policy中的完整memory schema；❌ 缺失memory叶子时填入新默认值。切换前排空旧在途对象，旧终态快照仅审计。
- **平台业务默认在业务操作边界取一次快照并冻结**：Runtime 在 Run 创建事务内（`_create_run`）**一次**取平台设置快照，把 `compaction` / `agent` 四叶 / 完整 `memory` schema / `artifact.max_archive_files` / `locale.default_timezone` 冻进 execution snapshot 的 `policy_json`；Worker 在新 Task / 投递 / 调度各边界各取一次；Gateway 在一条回复的生命周期边界取一次。执行期（每轮模型调用、每次工具执行）**不再重新读设置**（NFR-PERF-01），配置变更只影响后续新 Run/Task，执行中的对象继续用冻结值（`apps/agent-runtime/src/muad_agent_runtime/application/run_service.py` 的 `_create_run`；`tests/agent_runtime/test_execution_defaults_settings.py`）。**「操作边界取」而非「冻结进 Run」**的是不属于某个 Run/Task 的场景——如 Console 产物清理的 `artifact.retention_days`/`cleanup_batch_size` 在每次清理执行时取当前值（清理不是 Run）。
  - ✅ `snapshot_settings = fetch_snapshot(...)` 在 `_create_run` 里取一次、冻进 `policy_json`；执行器 / `context_builder` / tool set 只读冻结值
  - ❌ 在 `build_registry`/`load_history`/每轮模型请求里重新 `fetch_snapshot`（把设置读取拖进热路径，且同一 Run 前后可能读到不同 revision）
- **历史预算来自原policy.compaction.history_budget_tokens**：new/resume/continuation/Task均传同一冻结 token 预算给history builder；装配只读、按预算分页读取，请求边界只做一次保头保尾裁剪。普通USER/ASSISTANT、摘要与当前回合仍按组/预算保护，不得再引入消息条数、工具回合数或组数上限。✅ 按schema默认初始化、执行期显式传冻结值；❌ 独立硬编码条数/回合数或把旧键经真假判断换成默认值。
- **历史工具结果按来源 Run 与声明轮次配对**：同一调用 ID 可在不同轮次复用，结果只能匹配同 Run 中此前声明的调用，并随该声明原子回放；孤儿与同次调用的重复结果不回放。✅ 裁剪后保留最新完整往返及其真实结果；❌ 全会话按调用 ID 去重，丢失后续回合或回放旧结果。

## Avoid

- 违反上述任一规则的实现必须修复；与此 Spec 冲突的文档以本 Spec 与 `docs/` V1.4 为准。

## Applied Deltas

- {"source":"2026-11-15/scheduled-task-v2/scheduled-task-v2.md#TASK-009#DELTA-001","op":"add","rule":"RULE-001","payload_sha256":"cb42099d0b650c5050856f22f1f39c81f92e364f5bf6d23b9ee4aff96242efd7","at":"2026-11-16T05:09:15.751Z"}

- {"source":"2026-11-15/scheduled-task-v2/scheduled-task-v2.md#TASK-010#DELTA-001","op":"update","rule":"RULE-snapshot-001","payload_sha256":"79ca71e73965785f513e78275bdd783f5293a316a307372448964d743614602e","at":"2026-11-16T05:09:15.751Z"}

- {"source":"2026-11-21/agent-output-and-platform-defaults/agent-output-and-platform-defaults.md#TASK-007#DELTA-005","op":"add","rule":"RULE-002","payload_sha256":"c31b695ec19d13f2d7acc4c91bbede6d85f946a66fb4cc41ab852c8cd88f0f95","at":"2026-11-21T07:54:54.594Z"}

- {"source":"2026-11-24/skill-results-conversation-finalization/skill-results-conversation-finalization.md#TASK-004#DELTA-001","op":"add","rule":"RULE-003","payload_sha256":"5c797b6a277cd93fffd6ead1a546b41b55935c717d1aeb9845c9eca2926dea11","at":"2026-11-24T09:27:16.306Z"}

- {"source":"2026-11-23/conversation-context-retention/conversation-context-retention.md#TASK-001#DELTA-001","op":"add","rule":"RULE-004","payload_sha256":"ab1d8ffd91d48f91153b6601a1c803feaf450503d193b0c4e11dab034f7c909a","at":"2026-11-25T11:49:15.144Z"}

- {"source":"2026-11-26/agent-tool-failure-recovery/agent-tool-failure-recovery.md#TASK-001#DELTA-002","op":"add","rule":"RULE-005","payload_sha256":"3cfa9c44a658563bfb7e262c0c3f1136fb0cb70d56f5c416cfa63970b2acd879","at":"2026-11-26T12:11:20.731Z"}

- {"source":"2026-11-26/agent-tool-failure-recovery/agent-tool-failure-recovery.md#TASK-006#DELTA-005","op":"add","rule":"RULE-006","payload_sha256":"a031846d2bdadcc86d18f34e150240fde04de72d84df738e50aae3445933f182","at":"2026-11-26T12:11:20.732Z"}

- {"source":"2026-11-27/agent-tool-failure-chain/agent-tool-failure-chain.md#TASK-002#DELTA-001","op":"add","rule":"RULE-007","payload_sha256":"b2466af2968c9055e40f37c9258ff1f57855b4e54d5d0d42aeff19c72c0c8149","at":"2026-11-28T00:53:13.870Z"}

- {"source":"2026-11-27/agent-tool-failure-chain/agent-tool-failure-chain.md#TASK-005#DELTA-001","op":"add","rule":"RULE-008","payload_sha256":"affcbed29907af9bb37de52293127a8576e8926fd763b8302dab15c5a24dcfd2","at":"2026-11-28T00:53:13.871Z"}

- {"source":"2026-11-21/agent-execution-contracts/agent-execution-contracts.md#TASK-002#DELTA-001","op":"add","rule":"RULE-009","payload_sha256":"5a2dc2a49e5f7d211b07eacbcf64daf3b267d472eaf63a1820f6018bb6b26b48","at":"2026-11-28T01:04:28.947Z"}

- {"source":"2026-11-21/agent-execution-contracts/agent-execution-contracts.md#TASK-008#DELTA-001","op":"add","rule":"RULE-010","payload_sha256":"5c523609ec758497da21d78a5e4ac7d736a0b7fc506479381431e73c76c60258","at":"2026-11-28T01:04:28.948Z"}
