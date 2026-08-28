# ADR-A005 Durable Workflow 边界

WorkflowEngine Protocol 属于 Runtime Contract；durable state、retry、compensation、wait/signal、crash recovery 属于 Workflow Engine。

当前业务侧选择 DBOS 是已验证的实现决策，但保持可替换；Agent Runtime 不保存 workflow durable state。
