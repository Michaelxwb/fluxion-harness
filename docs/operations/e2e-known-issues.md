# E2E / 验收已知问题（2026-10-24）

口径：**全量验收与全量单测以 CI 为准**（`backend-static` + `backend-tests` 四个 PG/Redis 分片 +
`acceptance` + `smoke` / `e2e` job）。
本地只跑靶向套件；全量结果与失败用例由 CI 日志/artifact 记录，再在本地按“单跑复现 → 修因 →
复跑”的纪律解决。不要在本地为了“清干净”删 dev 数据或绕过断言。

## 已修复

- **DFX E-03 错误码误映射**（2026-10-24）：`SKILL_ARTIFACT_UNAVAILABLE` 未在
  `NON_RETRYABLE_CODES` 中，非幂等任务的 retry_safety 把它归成 `TASK_RESULT_UNVERIFIED`。
  已补登记；DFX 用例与 `tests/agent_worker/test_task_executor.py` 的回归单测均通过。
- **Redis claim 残留**：被 kill 的验收只靠 7200s TTL 释放；新增
  `scripts/clean_acceptance_claims.py`（仅在无活跃验收进程时清）并接到 `make acceptance`。
- **容量压测阻塞默认验收**：`test_capacity.py` 标记 `capacity_load`，默认 `make acceptance`
  排除；`make capacity-load` 显式跑 200 档。
- **失败不可定位**：根 `tests/conftest.py` 即时打印 `FAILED <nodeid> [phase]`，可选写
  `PYTEST_FAILURE_LOG`；heavy 命令加 `--durations=30`。
- **全仓 ruff 71 条既有债务**：已清理（`0206173c`），`make lint` 全绿；不要用 `ruff format`
  全仓重排。
- **后端单 job 串行**：已拆为 `backend-static` + `backend-tests` 四片（每片自带 PG/Redis）+
  独立 `acceptance` job；静态分片路径见 `.github/workflows/check.yml`，新增顶层测试目录须同步。

## 待解决（CI 暴露后再本地修）

- **全量单测的顺序/环境失败**：2026-10-24 本地 5620 条跑到 23% 出现 7 个 F，疑似跨用例污染/
  共享栈残留；用 `PYTEST_FAILURE_LOG` + `-x` 拿到首个用例后单跑复现。
- **acceptance 偶发红**：历史记录为“每次挂不同用例”，先按单跑/清 claim/停 dev 服务/清孤儿
  进程排查，不直接改实现。
- **1000 用户 × 8 轮容量**：manual/staging 未验证边界，不以 200 档结果冒充。
- **push/CI 实跑**：需要用户授权；本地已提交但未推送。
