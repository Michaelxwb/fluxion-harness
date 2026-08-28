# ADR-A004 Microkernel 与扩展信任边界

Kernel 只拥有执行生命周期、Context、typed events、contracts 和 orchestration。

可信基础设施实现可 in-process；不可信/业务扩展默认 MCP/RPC/Sandbox/isolated worker。

Tool 与 Plugin 分开建模：Tool 是调用能力，Plugin 是实现载体之一。
