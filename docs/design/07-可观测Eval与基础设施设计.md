# 07 可观测、Eval 与基础设施详细设计

## 1. Observability

统一关联：

`request_id → execution_id → trace_id → snapshot → model/tool/mcp/workflow events`

OTel 为标准输出契约。Audit 与普通日志分离。

## 2. Trace

InMemoryTraceStore 仅用于测试/本地。生产必须外置到 OTel backend/SQL TraceStore/其他共享后端；RuntimeInstance 重启不能让唯一审计证据消失。

## 3. Eval

EvalSet、EvalCase、EvalRun 与 AgentDefinition/ExecutionSnapshot exact version 关联。InMemory Eval store 仅 dev/test。Eval 结果应支持回归比较而不是一次性报告。

## 4. Storage

- Dev：SQLite，零额外数据库依赖；
- Prod：PostgreSQL 为 Resource/User/Session 等 SoT；
- 两者共享 schema/migration/repository contract tests。

Redis 只做 cache/event/coordination，不作为无法重建的唯一事实源。

## 5. Config Event

生产采用可靠 outbox/event 机制；事件用于 cache invalidation，不取代 Registry SoT。

## 6. 初始 SLO

沿用当前文档中合理的初始门槛：

- Runtime availability ≥ 99.95%；
- framework overhead P95 ≤ 50ms / P99 ≤ 100ms（不含 model/tool）；
- Resolver L1 P95 ≤ 5ms；
- Snapshot P95 ≤ 20ms；
- Hook dispatch P95 ≤ 10ms（不含 hook I/O）；
- Trace 关键路径关联完整率 ≥ 99%；
- tenant isolation 自动化越权成功数 = 0。

SLO 变化必须有 benchmark evidence。
