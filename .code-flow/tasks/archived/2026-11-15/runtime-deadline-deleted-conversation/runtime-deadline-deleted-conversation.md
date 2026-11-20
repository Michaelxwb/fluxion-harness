---
{"version":1,"requirement":"runtime-deadline-deleted-conversation"}
---

# Tasks: runtime-deadline-deleted-conversation

## TASK-001: 到期扫描能收尾「会话已删除」的 Run，且单行失败不拖住整批

- **Status**: done
- **Depends**:
- **Source**: dev runtime 日志 `run_deadline_sweep_failed`（`COMMON_NOT_FOUND` on `EventWriter._next_seq`）
- **Scenarios**:
- **Spec-Refs**: time/harness-time, persistence/harness-data, test/harness-test
- **Paths**: apps/agent-runtime/src/muad_agent_runtime/application/run_deadline.py, apps/agent-runtime/src/muad_agent_runtime/application/run_events.py, apps/agent-runtime/src/muad_agent_runtime/application/run_service.py, tests/agent_runtime/test_run_deadline_deleted_conversation.py
- **E2E**:
- **Skip-Tests**:
- **Skip-Reason**:

### Tests

```json
[
  {"id": "lint", "argv": ["uv", "run", "ruff", "check", "."], "timeout": 300},
  {"id": "typecheck", "argv": ["uv", "run", "mypy", "apps", "packages"], "timeout": 900},
  {"id": "deadline-regression", "argv": ["uv", "run", "python", "-m", "pytest", "-q", "tests/agent_runtime/test_run_deadline_deleted_conversation.py", "tests/agent_runtime/test_capacity_webchat_conversation_delete.py", "tests/agent_runtime/test_capacity_webchat_clock_lease.py", "tests/agent_runtime/test_run_events.py"], "timeout": 1800},
  {"id": "run-service", "argv": ["uv", "run", "python", "-m", "pytest", "-q", "tests/agent_runtime/test_run_service.py"], "timeout": 1800},
  {"id": "inventory", "argv": ["uv", "run", "python", "-m", "pytest", "-q", "tests/test_configuration_inventory.py"], "timeout": 600}
]
```

### Description

实机证据：dev 库里有一条 `RUNNING` 的 Run（`1822d120…`，2026-11-12 创建），它所属的 `runtime.conversation` 已被软删（`is_deleted=true`）。到期扫描把它选成候选后：

1. `EventWriter._next_seq` 的 WHERE 带 `Conversation.is_deleted.is_(False)`，取不到行 → `COMMON_NOT_FOUND`；
2. `expire_runs` 逐行循环没有单行隔离，异常直接冒到 `_deadline_loop`，**这一批里排在它后面的到期 Run 全部收不掉**，且每个 tick 重复一次（它就是按 `deadline_at` 排序的第一行）。

修复分三层：

- `EventWriter._next_seq` 与既有的 `_next_seq_range` 同口径：带 `allow_deleted_conversation` 时允许给软删会话追加（会话可见性由读路径的 `is_deleted` 判定，不由 seq 分配拦）；不带开关的普通追加仍然拒绝已删除会话（回归测试钉住这条）。
- 到期扫描与「排队超时」这两条终态写入用 `allow_deleted_conversation=True`：Run 在会话删除后仍必须能落终态，否则它会永远停在活跃态。
- `expire_runs` 单行 try/except + `logger.exception(extra={"run_id": ...})`：坏行只跳过自己，不再让整批饿死。

回归测试 `tests/agent_runtime/test_run_deadline_deleted_conversation.py` 三条：到期收尾成功并写出 RUN_FAILED、普通追加仍被拒而收尾追加放行、单行失败隔离（撤销修复即红）。

### Checklist

- [x] 到期扫描能收尾会话已软删的 Run，终态事件落库。
- [x] 普通追加仍拒绝已删除会话（开关不泛化）。
- [x] 单行失败不再打断整批。
- [x] 排队超时收尾同一口径。
- [x] 回归测试三条 + 邻近套件（conversation delete / clock lease / run events / run service）全绿。
- [x] 源码行号平移后配置盘点 CSV 同步（`tests/test_configuration_inventory.py`）。

### Spec Delta

```json
[]
```

### Log

- [2026-11-15] 由 dev runtime 的 `run_deadline_sweep_failed` 定位：会话软删后 Run 收尾无路可走；新增单行隔离。
- [2026-11-15] CI `backend-tests (fast)` 红：改动让 `ACTIVE_STATUSES`/`ADMISSION_CONCURRENCY_LIMIT`/`CANCEL_DB_FALLBACK_SEC` 的源码行号平移，配置盘点 CSV 失配。按 AST 重算行号（只改 line 列，类别与理由沿用），并把配置盘点测试加进本任务声明测试。
- [2026-11-15T01:52:50.680Z] started tests=d7ccdd101cb2

### Verification
- 2026-11-15T01:53:17.622Z PASS commands=4/4 executed=4 deduped=0 elapsed=24450ms tests=d7ccdd101cb2 head=1202888f0e85 dirty=1
  - lint PASS exit=0
  - typecheck PASS exit=0
  - deadline-regression PASS exit=0
  - run-service PASS exit=0
- 2026-11-15T02:22:51.581Z PASS commands=5/5 executed=5 deduped=0 elapsed=29891ms tests=ab3ccfb49151 head=d0d911ddc810 dirty=1 tests-changed
  - lint PASS exit=0
  - typecheck PASS exit=0
  - deadline-regression PASS exit=0
  - run-service PASS exit=0
  - inventory PASS exit=0
