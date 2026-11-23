# 核心功能文档（业务层）

本目录保存核心功能/业务层的详细设计，强调业务能力、规则、流程、状态与交付策略。

- 使用 `.code-flow/templates/design-module.md` 的 `module_kind: business`。
- 核心功能通过契约组合基础模块，不复制连接、存储、执行和审计实现。
- 结果与附件投递、PUSH 默认策略属于本层，不属于 Worker/Gateway/Connector 的框架机制。
- 分类与迁移状态见 `../00-详细设计索引与设计基线.md`。

当前文件：

- `20-Agent授权与Skill.md`
- `21-MCP与模型能力.md`
- `22-对话Run任务与调度.md`
- `23-结果与附件投递.md`
- `24-记忆经验审计与可观测.md`
