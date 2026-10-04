---
id: harness-snapshot
description: Agent Harness 通用平台规则：snapshot
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

- **快照 hash 口径统一为 `"sha256:"` + canonical JSON**：`json.dumps(sort_keys=True, separators=(",", ":"), ensure_ascii=False)` 后取 SHA256。contracts 侧 `snapshot_hash` 与 Runtime 侧 `_snapshot_hash` 必须同口径，否则跨服务比对不等（`packages/contracts/src/muad_contracts/snapshot.py:20,77-79`；`apps/agent-runtime/src/muad_agent_runtime/application/run_service.py:128-129,179-190`）。✅ 两侧都复用同一组 `sort_keys`/紧凑分隔符/`ensure_ascii=False`；❌ 任一侧漏掉其中一项（例如漏 `ensure_ascii=False` 导致中文被转义）→ hash 不等。
- **execution snapshot 的 `skills` 数组恒为 1**：只冻结本次 Task 要执行的那一个 Skill，执行器按 `skill_artifact_id` 精确匹配（`packages/contracts/src/muad_contracts/snapshot.py:4-5,21-28,53-74`）。❌ 把 Agent 的全部 Skill 塞进 `skills` 会导致执行器误执行非本次目标的 Skill。
- **`api_key` 必须从快照与 hash 中剥离**：`model_json` 与 `content_hash` 的输入都要 `pop("api_key")`，认证实时走 API-09 读取（`apps/agent-runtime/src/muad_agent_runtime/application/run_service.py:172-176`；断言 `tests/agent_runtime/test_snapshot_freeze.py:33-46`）。这是 snapshot 与 secret 两个 spec 的接缝：✅ 快照只留 `base_url`/`model_id` 等非密钥字段；❌ 密钥进快照或进 hash，会造成密钥轮换即 hash 漂移且密钥落盘。
- **非终态写入同样是 CAS**：「终态写入必须 CAS」不限于终态：resume 用 `status == WAITING_INPUT` 作条件把 `WAITING_INPUT→RUNNING`，落败者拿 `RUN_BUSY`；cancel 同样以预期状态为条件（`apps/agent-runtime/src/muad_agent_runtime/application/run_service.py:673-686,765-806`）。✅ `UPDATE ... WHERE id = ? AND status = <预期>`；❌ 先读后写、或只按 id 更新。
- **「配置或授权变更只影响后续新 Run/Task」由 resume 不重 resolve 承载**（resume 从已落库快照重建 Agent/Model/Skill/MCP，`apps/agent-runtime/src/muad_agent_runtime/application/run_service.py:642-651`）。**该规则代码证实但验收缺位**：指定 verifier `tests/agent_runtime/test_snapshot_freeze.py` 只有 hash 稳定性与 `api_key` 剥离两例，没有「变更配置后旧 Run 仍用旧快照」的断言。

## Avoid

- 违反上述任一规则的实现必须修复；与此 Spec 冲突的文档以本 Spec 与 `docs/` V1.4 为准。
