# 基础模块文档（框架层）

本目录保存基础模块/框架层的详细设计，强调通用机制、架构、数据、接口与质量，不承载具体业务策略。

- 使用 `.code-flow/templates/design-module.md` 的 `module_kind: framework`。
- 依赖方向：基础模块不依赖核心功能；核心功能通过契约组合基础模块。
- 分类与迁移状态见 `../00-详细设计索引与设计基线.md`。

当前文件：

- `03-Console-Platform.md`
- `04-Agent-Runtime.md`
- `05-IM-Gateway与Connector.md`
- `06-执行内核与Skill运行时.md`
- `10-Agent-Worker与异步任务.md`
- `12-平台接入与Session.md`
