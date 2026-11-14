---
{"version":1,"requirement":"test-datastore-isolation"}
---
# Tasks: test-datastore-isolation

<!--
一个需求一份任务文档。每个 TASK 使用一个 `## TASK-NNN: 标题` 段。
不要把 TASK 拆成单独文件。
-->

## TASK-001: 全部 pytest 用例跑在模板库克隆上，不再触碰 dev 库

- **Status**: done
- **Depends**:
- **Source**: .code-flow/specs/test/harness-test.md#验收隔离
- **Scenarios**:
- **Spec-Refs**: test/harness-test
- **Paths**: tests/conftest.py, tests/async_tool_datastores.py, tests/acceptance/datastores.py, tests/acceptance/conftest.py, tests/migrations/conftest.py, tests/agent_runtime/conftest.py, tests/console_platform/conftest.py, tests/agent_core/conftest.py, pyproject.toml
- **E2E**:
- **Skip-Tests**:
- **Skip-Reason**:

### Tests

```json
[
  {
    "id": "console-family",
    "argv": ["uv", "run", "python", "-m", "pytest", "-q", "tests/console_platform/test_user_memory_api.py"],
    "timeout": 300
  },
  {
    "id": "clone-family",
    "argv": ["uv", "run", "python", "-m", "pytest", "-q", "tests/agent_runtime/test_memory_writer.py", "tests/migrations/test_memory_schema.py"],
    "timeout": 600
  },
  {
    "id": "own-datastore-family",
    "argv": ["uv", "run", "python", "-m", "pytest", "-q", "tests/acceptance/runtime/test_memory_acceptance.py", "tests/acceptance/im_gateway/test_memory_security.py"],
    "timeout": 600
  }
]
```

### Description

现状：只有 `async_tool_database` / acceptance 栈用临时库；`tests/agent_runtime`、`tests/console_platform`、`console_*`、`agent_worker` 等大量套件直连 dev 的 `muad` 库与 Redis DB 0，靠租户隔离。表现为：测试与 dev 服务互相抢 Worker/租约/缓存，跑测前必须停 dev。

目标：**每个 pytest 用例默认跑在独立的 `muad_acc_*` 克隆库 + 独立 Redis 号位上**，与 dev 完全隔离；需要自建库的套件（acceptance 栈、指定 revision 的迁移测试）显式标记 `own_datastore`，纯逻辑目录标记 `no_datastore` / `unit`。

做法：

1. `tests/acceptance/datastores.py` 增加 `create_template()`（会话级 `muad_tpl_*`，迁移一次）、`clone_datastore()`（`CREATE DATABASE ... TEMPLATE`，约 150ms）、`drop_template()`。
2. 根 `tests/conftest.py`：会话级 `datastore_template` + 函数级 `isolated_datastore`（克隆 → `dispose_engine()` → `reset_settings_cache()` → 设 `DATABASE_URL`/`REDIS_URL` → 清理）与 autouse 守卫；非隔离路径直接断言失败。
3. `async_tool_datastore` 复用该克隆（`own_datastore` 套件回退到私有克隆）。
4. `agent_runtime` / `console_platform` 的 `database_guard` 改为依赖 `isolated_datastore`，删除“检查 dev 库已迁移”的旧逻辑。
5. 新增 `own_datastore`（acceptance、migrations）与 `no_datastore`（agent_core）marker。

### Checklist

- [x] `create_template` / `clone_datastore` / `drop_template` 实现，克隆走 `_run_in_thread`（禁止 `asyncio.run` 污染主循环）。
- [x] 根 conftest 的 `isolated_datastore` 设置 env 并清理 settings/engine 缓存，autouse 守卫断言非隔离库立即失败。
- [x] `async_tool_datastore` 复用克隆；acceptance/migrations 标 `own_datastore`；agent_core 标 `no_datastore`。
- [x] `agent_runtime` / `console_platform` 的 `database_guard` 不再检查 dev 库。
- [x] 纯逻辑测试（contracts/taxonomy/evaluation）标 `unit`，不付克隆成本。
- [x] 代表性三类套件全绿：console 直连族、原临时库族、自建库族。
- [x] 实测：原临时库用例显著提速（`test_memory_writer` 7 条 15.6s → 4.7s），纯逻辑套件无额外开销。
- [x] 跑测期间 dev 库行数不变、无孤儿库/Redis claim。

### Spec Delta

```json
[
  {
    "id": "DELTA-001",
    "op": "update",
    "target": "test/harness-test",
    "ref": "RULE-test-001",
    "before": "跨 API/DB/Runtime/Browser 的关键流程必须 E2E 且明确“不得 mock 的真实边界”（真实 PostgreSQL、真实 Redis 行为、真实 HTTP、真实浏览器渲染）；单元测试覆盖纯逻辑与状态机，契约测试覆盖枚举/错误码/迁移一致性。",
    "rule": "跨 API/DB/Runtime/Browser 的关键流程必须 E2E 且明确“不得 mock 的真实边界”（真实 PostgreSQL、真实 Redis 行为、真实 HTTP、真实浏览器渲染）；单元测试覆盖纯逻辑与状态机，契约测试覆盖枚举/错误码/迁移一致性。所有 pytest 用例默认跑在会话模板库（`muad_tpl_*`，迁移一次）的每用例克隆 `muad_acc_*` 与独立 Redis 号位上，进程 env 同步指向克隆，非隔离解析必须显式失败；只有自建库的套件（acceptance 栈、指定 revision 的迁移测试，标记 `own_datastore`）与纯逻辑目录（标记 `no_datastore`/`unit`）例外。因此测试不得读写 dev 库或 dev Redis 号位，跑测无需先停 dev 服务。",
    "evidence": "TASK-001；根 conftest 的 isolated_datastore 与守卫"
  }
]
```

### Log

- [2026-11-15] created after the dev-DB isolation review.

### Verification

- 尚未执行
- 2026-11-14T18:09:54.450Z PASS commands=3/3 executed=3 deduped=0 elapsed=17212ms tests=481f8c69b318 head=9dcf6e552fc7 dirty=1
  - console-family PASS exit=0
  - clone-family PASS exit=0
  - own-datastore-family PASS exit=0
