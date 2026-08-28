# 历史问题索引

> 本文件只保存问题证据，不再直接规定 Architecture
> Response。当前规范需求见 `foundation/02-核心需求.md`。

| ID | 历史问题 | 当前问题域 |
|----|---------|-----------|
| P01 | 配置修改要求 Runtime 重启 | Runtime/配置 |
| P02 | Runtime 持有 durable facts | Runtime |
| P03 | Skill/MCP 挂 Agent 导致用户数据碎片化 | Capability/User |
| P04 | Dev/Prod 配置模型分裂 | Storage |
| P05 | 逻辑 Agent 生命周期耦合 Pod | Runtime |
| P06 | 本地缺少实用配置入口 | Product/DevEx |
| P07 | 热更新导致执行中配置漂移 | Execution |
| P08 | 每增加能力 Core 膨胀 | Extensibility |
| P09 | 安全/审计/审批侵入 Executor | Security |
| P10 | In-process Plugin 扩大信任边界 | Security |
| P11 | LLM Tool Chain 不是 durable workflow | Workflow |
| P12 | Tool/Workflow/API 重复业务逻辑 | Capability |
| P13 | 渠道用户与内部用户身份割裂 | Identity |
| P14 | Schema-valid 仍可能语义错误 | Security |
| P15 | 审批过多造成疲劳 | Governance |
| P16 | 用户拥有 MCP 不代表任意 Agent 可调用 | Capability |
| P17 | Agent 私有 Profile 造成事实不一致 | User |
| P18 | 一次性 Eval 会过时 | Eval |
| P19 | 安全层可能破坏延迟 | DFX |
| P20 | 多 Agent 直接代码耦合不可扩展 | A2A |
| P21 | SOP 写死 Python 阻碍版本化治理 | Workflow |
| P22 | 架构模式可能变成 cargo cult | Governance |
| P23 | typed Policy 与运行时字段漂移 | Spec SoT |
