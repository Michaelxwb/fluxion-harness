# ADR-A003 ExecutionSnapshot 一致性

Execution 开始时固定所有影响行为的资源/绑定/策略版本。执行中发布只影响下一次 Execution。

Snapshot 必须不仅能算 digest，还能追溯实际 EffectiveCapability 与相关 Binding/Policy exact version。
