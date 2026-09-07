# ADR-A014 执行终态与清理契约

**引用**：source-review P1-03、TASK-013、规则 25。

**背景**（2026-09-08 核实，`services/runtime_app.py:242-312`）：

- `run()` 成功与 `except Exception` 双路径各调一次 `finish_execution`，无"恰好一次终态"保证；
- `except Exception` 覆盖不到 `CancelledError`/`GeneratorExit`（取消/关闭绕过清理）；
- 异常路径的 `_append_trace` 若再失败，会覆盖原始业务错误；
- `ExecutionSession` 仅有 `prepare()`，无 finalizer 所有者；清理无预算（shield/超时未定义）。

**决策**：

1. **四类终态**：`completed` / `failed` / `cancelled` / `timed_out`。映射：无异常 → completed；`CancelledError`/`GeneratorExit` → cancelled；`TimeoutError`（含 `asyncio.TimeoutError`）→ timed_out；其余 → failed。超时与取消必须区分（客户端取消 ≠ 模型超时）。
2. **恰好一次**：单次执行恰好一个业务终态。重复结束（重复 finalize/finish）幂等，后到者 no-op；冲突结束（已 completed 后清理又失败）保留首次业务终态——`first_terminal_wins`。
3. **finalizer 所有者**：`ExecutionSession`。`prepare()` 创建 context 即获得所有权，即使后续 Model/MCP 准备失败也必须进入清理（TASK-014 实现）。
4. **prepare 部分失败**：已创建资源按逆序释放；释放本身有界。
5. **部分输出保存**：已产生的 token/message 保留（trace/partial），终态仍按异常映射（不因"有输出"伪造 completed）。
6. **清理预算**：`FINALIZE_BUDGET_MS = 5_000`；有限 shield 必须带 timeout，不能无限延长客户端取消。
7. **Trace 写失败**：可观测（error log + trace event，带 IDs），不覆盖原始业务错误，不伪造完成记录。
8. **取消语义**：`CancelledError`/`GeneratorExit` 可捕获做有界清理，但必须重新传播；SIGKILL 不承诺 finally。
9. **业务终态 vs 清理失败**：清理失败独立记录（`cleanup_error`），永不改变已确定的业务终态。
10. 不新增 Runtime 本地持久执行真相（规则 1）。

**后果**：TASK-013 在 `runtime_contracts.py` 落终态类型 + 映射/合并纯函数 + contract 测试；TASK-014/015/016 按本契约实现。
