---
id: harness-snapshot
description: Run/Task 快照冻结
stages:
- design
- plan
- code
- review
enforcement: required
verifiers:
- rule: RULE-snapshot-001
  type: command
  config:
    argv:
    - bash
    - -lc
    - uv run pytest -q tests/agent_runtime/test_snapshot_freeze.py tests/agent_runtime/test_run_reaper.py && uv run pytest -q tests/agent_runtime -k "executor or resolve"
    cwd: .
    timeout: 600
---

# harness-snapshot

## Rules

- [RULE-snapshot-001] 每个新 Run/Task 在执行前冻结 RuntimeSnapshot/execution snapshot（Agent/Model/Skill/MCP 版本、Prompt 模板版本、catalog revision/hash；**预算只属 execution snapshot 的 `budget` 键**——Run 侧的 `RuntimeSnapshot` 无 budget 列，等价载体是 `policy_json`，默认含 `max_model_retries`，预算与压缩策略等执行期策略一并冻结在此键）；配置或授权变更只影响后续新 Run/Task；终态写入必须 CAS。

## Conventions

- **异步容量与绝对 deadline 在 Run 创建时冻结**：policy_json.async_tools 由严格 AsyncToolPolicy 与 Agent 覆盖构建；deadline_at 从首次 start_time 与有效 AgentPolicy 计算。Runtime `async_tools/operations.py` 在 Conversation→Run 锁序内核对冻结能力、按真实 call 去重并预留容量，再同事务写 operation + SUBMIT outbox；取消、失败和 Reaper 同事务写 JOIN/未受理提交的取消意图。✅ 检查点恢复读取原 policy/deadline；❌ 在提交或恢复时重新取设置、延期 deadline，或在 HTTP 中持有业务行锁。

- **快照 hash 口径统一为 `"sha256:"` + canonical JSON**：`json.dumps(sort_keys=True, separators=(",", ":"), ensure_ascii=False)` 后取 SHA256。contracts 侧 `snapshot_hash` 与 Runtime 侧 `_snapshot_hash` 必须同口径，否则跨服务比对不等（`packages/contracts/src/muad_contracts/snapshot.py`；`apps/agent-runtime/src/muad_agent_runtime/application/run_service.py` 的 `_canonical_json` / `_snapshot_hash`）。✅ 两侧都复用同一组 `sort_keys`/紧凑分隔符/`ensure_ascii=False`；❌ 任一侧漏掉其中一项（例如漏 `ensure_ascii=False` 导致中文被转义）→ hash 不等。
- **execution snapshot 的 `skills` 数组恒为 1**：只冻结本次 Task 要执行的那一个 Skill，执行器按 `skill_artifact_id` 精确匹配（`packages/contracts/src/muad_contracts/snapshot.py`）。❌ 把 Agent 的全部 Skill 塞进 `skills` 会导致执行器误执行非本次目标的 Skill。
- **`api_key` 必须从快照与 hash 中剥离**：`_snapshot_model` 把 `model_json` 的 `api_key` 弹掉，认证实时走 API-09 读取（`apps/agent-runtime/src/muad_agent_runtime/application/run_service.py` 的 `_snapshot_model`；断言 `tests/agent_runtime/test_snapshot_freeze.py` 的 hash 与 api_key 用例）。这是 snapshot 与 secret 两个 spec 的接缝（密钥不进快照由 RULE-secret-001 拥有）：✅ 快照只留 `base_url`/`model_id` 等非密钥字段；❌ 密钥进快照或进 hash，会造成密钥轮换即 hash 漂移且密钥落盘。
- **非终态写入同样是 CAS**：真实入站 resume 在 Conversation→Run→Interrupt 锁序内，用 `status == WAITING_INPUT` 与绝对 deadline 作条件置为 `CREATED`；PG claim 再获得执行 epoch/lease。输入、确认效果、interrupt消费及 submission 与该 CAS 同事务。✅ `UPDATE ... WHERE id = ? AND status = <预期>`；❌ 先读后写、或只按 id 更新。
- **旧 Run resume 不重新 resolve 定义或设置**：从原快照重建 Agent/Model/Skill/MCP，凭据走实时 API-09。`test_snapshot_freeze.py` 实测定义单腿变化与旧行逐列不变；`test_memory_redesign_snapshot.py` 实测设置单叶变化、新Run工具历史为0、旧resume仍保留原6轮。✅ 原policy中的完整memory schema；❌ 缺失memory叶子时填入新默认值。切换前排空旧在途对象，旧终态快照仅审计。
- **平台业务默认在业务操作边界取一次快照并冻结**：Runtime 在 Run 创建事务内（`_create_run`）**一次**取平台设置快照，把 `compaction` / `agent` 四叶 / 完整 `memory` schema / `artifact.max_archive_files` / `locale.default_timezone` 冻进 execution snapshot 的 `policy_json`；Worker 在新 Task / 投递 / 调度各边界各取一次；Gateway 在一条回复的生命周期边界取一次。执行期（每轮模型调用、每次工具执行）**不再重新读设置**（NFR-PERF-01），配置变更只影响后续新 Run/Task，执行中的对象继续用冻结值（`apps/agent-runtime/src/muad_agent_runtime/application/run_service.py` 的 `_create_run`；`tests/agent_runtime/test_execution_defaults_settings.py`）。**「操作边界取」而非「冻结进 Run」**的是不属于某个 Run/Task 的场景——如 Console 产物清理的 `artifact.retention_days`/`cleanup_batch_size` 在每次清理执行时取当前值（清理不是 Run）。
  - ✅ `snapshot_settings = fetch_snapshot(...)` 在 `_create_run` 里取一次、冻进 `policy_json`；执行器 / `context_builder` / tool set 只读冻结值
  - ❌ 在 `build_registry`/`load_history`/每轮模型请求里重新 `fetch_snapshot`（把设置读取拖进热路径，且同一 Run 前后可能读到不同 revision）
- **历史工具预算来自原policy.compaction.max_history_tool_rounds**：new/resume/continuation均传同一冻结值给history Builder，按完整 assistant/tool 回合保留；0省略全部历史工具回合，普通USER/ASSISTANT及摘要不受该预算影响。✅ 按schema默认初始化、执行期显式传冻结值；❌ 独立硬编码6或把0经真假判断换成默认值。

## Avoid

- 违反上述任一规则的实现必须修复；与此 Spec 冲突的文档以本 Spec 与 `docs/` V1.4 为准。
