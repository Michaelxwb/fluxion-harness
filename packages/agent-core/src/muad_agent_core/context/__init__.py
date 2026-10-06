"""上下文装配与压缩的纯逻辑（运行时无关）。

- `compactor.py`：会删消息的压缩层与「受保护前缀」切分；
- `summary.py`：摘要字段的渲染与解析。

**这里没有 `ContextBuilder`**（2026-10-07 删除）：它是一份**从未接线**的接口——全仓没有调用方，
git 历史里也从未有过（`git log -S'_context_builder.build('` 为空）。生产的两处装配是
`AgentRunner`（system 提示，经 `DefaultPromptBuilder`）与 `RunService._load_history`
（memory / 摘要 / 历史）。此前它自带一套 `ContextInput`（技能正文、产物预览、工具 schema 都当消息
塞），与生产不一致而且测试在为它作证——装配语义因此有三份，其中一份从不生效。
"""
