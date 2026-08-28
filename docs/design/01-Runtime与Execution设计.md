# 01 Runtime 与 Execution 详细设计

## 1. 目标

在不绑定 RuntimeInstance 的前提下执行任意 AgentDefinition；保持 Execution 内版本稳定；允许水平扩展。

## 2. 当前可复用实现

当前 `ResourceResolver/ExecutionSnapshotBuilder`、`RuntimeContext`、`AgentRuntime`、Registry/Hot Reload 方向正确。`MemoryManager._l0` 属于 execution-local 状态，可保留。

## 3. Execution 生命周期

```text
Request
 → Identity resolve
 → AgentDefinition resolve
 → RuntimeProfile resolve
 → User Context resolve
 → Capability resolve
 → Policy/model resolve
 → ExecutionSnapshot freeze
 → per-execution ToolRuntime/MCP prepare
 → AgentLoop
 → flush memory/trace
 → finish
```

## 4. Snapshot

Snapshot 应冻结 exact versions，不保存 Secret 明文。所有运行中二次查询必须使用 Snapshot pin 或 ExecutionContext 首次解析缓存，禁止 latest-published 漂移。

## 5. Runtime 本地允许状态

允许：L0、connection pool、MCP HTTP pool、bounded resource cache、provider implementation registry、circuit-breaker 瞬时计数。

不允许作为 SoT：Session、Profile、Grant、Approval、Schedule、Workflow Run、Published Resource、Personal Memory。

## 6. Scheduler

当前 RuntimeScheduler 本地任务集合只允许 test/dev。生产 Scheduler 采用 durable task store + lease/claim；触发时构造新的 ExecutionRequest，Runtime 不拥有 schedule lifecycle。

## 7. Hot Reload

Registry 是 SoT。revision/event 仅用于失效 Cache；事件丢失不能造成永久错误，Runtime 必须能通过 revision/poll/reload 自愈。

## 8. Stateless Gate

必须部署至少两个 RuntimeInstance，关闭 sticky session，验证跨 Pod Snapshot/EffectiveCapability/Session 一致，并执行 kill、scale、rolling restart。
