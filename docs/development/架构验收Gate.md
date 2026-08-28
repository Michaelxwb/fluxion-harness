# 架构验收 Gate

## G1 Per-user Capability

同一 AgentDefinition，User-A/User-B 配置不同 Tool/MCP/Skill、版本、参数、enable、CredentialRef。验证实际 Tool list 与调用结果均不同且正确。

负向矩阵：UserGrant 缺失、AgentAllow 缺失、Tenant deny 任一情况均拒绝。

## G2 Credential Isolation

A/B 使用同一 MCP Definition、不同 CredentialRef。跨用户/跨租户读取、连接池 key、cache key 均不得串用。

## G3 Multi-Pod Stateless

真实部署 ≥2 RuntimeInstance，无 sticky session。R1→A、R2→B，kill A，继续 B，扩容、rolling restart、缩容。不得丢 Agent/User/Session/Memory/Binding/Credential/Approval/Workflow facts。

## G4 Execution Immutability

Execution-1 pin v1；运行中发布 v2；Execution-1 全程 v1；Execution-2 使用 v2。

## G5 Local State Audit

扫描所有 Runtime 进程内 dict/list/cache，标注 Ephemeral/Cache/Durable/SoT。Durable/SoT 只在本地即失败。

重点：Scheduler、Trace、Approval、Eval、Workflow Stub。

## G6 Tenant Isolation

Resource、Binding、Profile、Memory、Secret、Approval、Trace 全部执行跨 tenant negative test，越权成功数 0。

## G7 Control/Execution Plane

停 Console 后已发布 Agent 继续运行；创建 AgentDefinition/RuntimeProfile 不创建固定 Pod；Runtime 不调用 Console API 获取配置 truth。

## G8 Security Write Path

Schema valid 但 semantic invalid 拒绝；高风险写操作命中审批；LLM 无法自降风险；未授权参数不进入敏感 Hook。

## G9 Store Contract

SQLite/PostgreSQL 使用同一 fixture 跑 Registry/User/Session/Binding contract suite，行为一致。
