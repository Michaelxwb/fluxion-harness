# ADR-A015 Runtime 错误契约：slug 独立 error 字段 + 整数码映射

**引用**：source-review P1-04、TASK-019、规则 25、不直接照搬外部示例。

**背景**（2026-09-08 核实）：

- `errors/console.py`：`RUNTIME_APPLICATION_ERROR = 40_001`，注释写明"slug 保留在 envelope message 中以便追溯"——即 HTTP slug 拼在 message（P1-04 核查结论仍成立）；
- `http_runtime_gateway.py:245-246` 解码 `envelope.get("error")`，但 `responses.failure()` 从不写 `error` 字段——Gateway upstream_error 在 HTTP 路径恒为 None（丢失仍成立）；
- sse-streaming-contracts 已验收：streaming error 事件有独立 error 字段（upstream_code + slug 保留，GREEN）。本 ADR 以该结论为输入，只定 HTTP 侧，不重复设计 streaming 语义。

**决策**：

1. **保留四字段 envelope 语义并加法扩展**：Runtime HTTP 信封为 `{code, message, data, request_id, error}`，`error: string | null` 为稳定机器可读 slug（snake_case）。Console envelope 保持四字段（规则 22 不动）；Runtime 加字段是加法，老客户端忽略未知字段。`ApiResponse.extra` 保持 forbid（服务端视角），客户端按兼容矩阵解析。
2. **slug 位置**：独立 `error` 顶级字段，与 SSE error 事件同一 slug 词汇。`RUNTIME_APPLICATION_ERROR` 的"slug 拼 message"做法废弃：slug 进 `error`，`message` 只留安全人类文案。
3. **整数码映射**：保留现有码值（不照搬外部示例）：`code` 为业务码（沿用 30xxx–46xxx 命名空间），HTTP status 表 HTTP 语义，二者不互相替代。`RuntimeApplicationError.code`（字符串 slug）→ `failure(error=slug)` 透传；整数码由调用方按现有常量表选择。
4. **安全文案**：未知异常 message 用固定安全文案，不把异常原文（含 SQL/DSN/Secret/堆栈）加入响应；原文只进日志（脱敏）。
5. **旧载荷兼容矩阵**（解码侧由 TASK-021 落，本 ADR 定语义）：
   - 无 `error` 字段的老载荷 → slug 回落 `unknown_error`，不得用 message 文本反推；
   - 畸形（非 JSON/缺 code/类型错）→ 稳定网关错误，不抛原始解析异常；
   - HTTP200 内嵌 SSE error（旧兼容）→ 按 error 事件处理，见 TASK-021。
6. **兼容窗口**：加字段立即生效（加法兼容）；"slug 拼 message"的旧写法在 TASK-020 中迁移，迁移完成前解码侧双读（`error` 优先，缺失回落 unknown，不读 message）。

**后果**：TASK-019 在 `responses.py` 加 `error` 类型契约 + contract 测试；TASK-020 迁移所有 Runtime handler；TASK-021 落解码。
