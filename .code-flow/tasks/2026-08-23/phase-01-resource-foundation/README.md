# Fluxion Resource 与 Registry 基础

本目录完全使用 code-flow 原生命令。

启动/继续任务：

```text
cf-task-start resource-foundation TASK-001
```

查看任务状态：

```text
cf-task-status resource-foundation
```

不要手工维护 `.active-task.json`；`cf-task-start` 会负责 Context refresh、Start Gate、active marker、Task Session、RED/GREEN 和验收证据。
