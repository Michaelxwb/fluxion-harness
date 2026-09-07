# ADR-A016 Snapshot 一致读：ScopedRead 契约 + 只读事务分段

**引用**：source-review P2-02、TASK-024、跨任务约束（REPEATABLE READ / 双读 Revision）、规则 25。

**背景**（2026-09-08 核实，`registry/store.py`）：

- `RegistryReadStore` Protocol 已有 `get`（tenant ✓）、`read_revision`（tenant ✓）、`list_bindings`（tenant ✓），但无"一致视图"概念：多次 `get` 可落在不同 revision（混合发布窗口）；
- 无只读事务声明（REPEATABLE READ 未实现），无超时/错误类型契约；
- Credential 解析 / Memory I/O 与配置读无分段约束（外部 I/O 可持有配置事务）。

**决策**：

1. **ScopedRead Contract**（新增，不改现有 Protocol）：`ScopedReadStore(RegistryReadStore)` + `begin_scoped_read(*, tenant_id, timeout_ms)`（async context manager）→ `ScopedRegistryReader`（`get` / `read_revision` / `list_bindings`，语义为 revision-pinned）。现有实现不受影响，迁移由 TASK-025 落。
2. **只读事务**：PG `REPEATABLE READ` 只读事务；同一快照内全部配置读走同一事务/同一 revision。count 与分页用同一过滤集合（既有 database.md 规范）。
3. **分段**：事务内只做配置读；Credential 解析、Memory I/O、外部 HTTP/RPC 在事务外。外部 I/O 禁止持有配置事务（database.md Avoid 已有，本 ADR 重申到快照场景）。
4. **精确版本固定**：读到的 version pins 即快照版本；双读 Revision 若不能覆盖全部写入路径不得声称一致（跨任务约束原文）。
5. **有界失败**：`ScopedReadTimeoutError` / `ScopedReadConflictError`（均带字符串 `code`，`RegistryStoreError` 子类）；超时/冲突为类型化失败，无无穷重试。
6. **缓存**：只收完整、已校验结果；异常不污染缓存；缓存语义同时满足 B-ID-02（与 TASK-006 互引，既有约定）。

**后果**：TASK-024 在 `store.py` 落 Protocol + 错误类型 + contract 测试（仅契约校验）；TASK-025 实现 PG 行为；TASK-026 并发验收。
