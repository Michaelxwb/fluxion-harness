# 04 Workflow 与主动任务详细设计

## 1. 边界

AgentLoop 不承担 durable workflow。Workflow 通过粗粒度 Tool/Adapter 暴露给 Agent。

当前 `WorkflowEngine` Protocol 的 start/resume/signal/cancel/get_status 方向保留；`ResilientWorkflowEngine` 的 timeout/retry/breaker 是调用侧韧性，不等于 workflow durability。

## 2. DBOS

当前已完成 DBOS PoC 并选定为业务侧 durable backend。设计上保持 Protocol 隔离，未来跨语言/规模要求变化时可重新评估。

## 3. Snapshot 与长流程

Workflow 启动时记录所需 Agent/Capability/Workflow exact version refs。长时间 resume 需要 pinned resource retention/GC policy，不能只依赖"latest"。

## 4. Workflow Definition

开源 Harness 可定义 Workflow Adapter Contract；业务 Workflow DSL/Definition 是否进入开源仓，应以"跨公司可复用性"判断。当前默认业务 SOP/DSL 属于业务接入层。

## 5. Scheduler

主动任务与 Workflow 定时器都不能依赖 Runtime 本地 dict。ScheduledTask 至少持久化：task_id、tenant/user、agent/workflow ref、schedule、next_run、status、version、lease owner/expiry、idempotency key。

## 6. 幂等

不可逆副作用必须有 idempotency key；worker crash/retry 不得造成 committed step 重复。
