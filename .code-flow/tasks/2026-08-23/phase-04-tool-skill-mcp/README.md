# Fluxion Tool Skill MCP

本目录完全使用 code-flow 原生命令。

启动/继续任务：

```text
cf-task-start tool-skill-mcp TASK-004
```

查看任务状态：

```text
cf-task-status tool-skill-mcp
```

不要手工维护 `.active-task.json`；`cf-task-start` 会负责 Context refresh、Start Gate、active marker、Task Session、RED/GREEN 和验收证据。
