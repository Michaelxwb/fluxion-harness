# ADR-A006 Typed Spec 单一真相源

保留旧 ADR-012 的正确部分：Pydantic typed spec 是校验、Runtime 消费和 Console schema 的唯一字段事实源；禁止运行路径通过 raw `spec_json.get` 创建第二套 schema。

撤销旧 ADR-012 中"删除无效 MCP tools 字段后 user 维度与 agent 维度重合"的推导。删除死字段不等于删除 UserGrant 这一授权维度。
