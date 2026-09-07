# ADR-A012 执行身份契约：唯一创建者、格式与缺省规则

**引用**：source-review P1-01、TASK-004、ARCH-02、规则 25（Contract 变更须经 ADR）。

**背景**（2026-09-08 核实）：

- `RunRuntimeRequest.request_id/trace_id/execution_id` 默认为原始 `uuid4().hex`（`services/runtime_contracts.py:77-79`），无格式约束、无校验；
- middleware 用 `req_<hex>` / `trace_<hex>`（`api/middleware.py:115-124`），`context_resolver.py:143-144` 用 `exec_<hex>` / `trace_<hex>`，三处格式不统一；
- HTTP 入口与 Resolver 各自重建 ID，无"唯一创建者"，下游无法区分"调用方传入"与"本层生成"（P1-01 核查结论）。

**决策**：

1. **唯一创建者**：受信入口（HTTP 边界：middleware + 构造 `RunRuntimeRequest` 的 handler/gateway）是唯一可接受外部 ID 并补齐缺省的地方。内部层（ContextResolver / SnapshotBuilder / ExecutionSession / AgentRuntime）禁止创建或替换身份，缺失即 fail-closed（`ValueError`，由调用方转为 500 内部错误，不伪造身份继续执行）。内部改造由 TASK-006 落，本 ADR 只定契约。
2. **格式**：`req_<32hex>` / `trace_<32hex>` / `exec_<32hex>`，正则 `^(req|trace|exec)_[0-9a-f]{32}$`。非法格式 → 边界 400 fail-closed（`request_identity_invalid`），不静默替换（静默替换会制造"身份被换掉却无感知"的同类问题）。
3. **位置与优先级**：
   - `request_id`：`X-Request-ID` header → body → 生成。header 与 body 皆有且不同 → 400（防歧义）。header 沿用语义不变（现有行为保留）。
   - `trace_id`：`X-Trace-ID` header → body → 生成；同上冲突规则。
   - `execution_id`：body → 生成（不设 header；单次执行缺省总是新的——重试/重放必须显式复用旧值，由调用方负责）。
4. **缺省**：仅受信入口可补齐。旧客户端（不发 ID 字段）→ 入口按格式生成，前向兼容，无需版本窗口。内部层不得以"兼容"为名补齐。
5. **请求身份不纳入配置 digest**（重申；校验由 TASK-006 B-ID-02 落）。

**后果**：

- `runtime_contracts.py` 新增 `RunIdentity` 类型化契约 + `resolve_request_identity()`（header/body 合并）+ `validate_*` 校验器；TASK-004 只做契约层，不接 HTTP（接入由 TASK-005 落）。
- `runtime/context.py` 的裸 hex 默认与 `middleware` 的 strip-即信任需在 TASK-005/006 中收敛到本契约，本 ADR 先行冻结格式。
