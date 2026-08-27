# Fluxion 无状态 Runtime Kernel

本目录完全使用 code-flow 原生命令。

启动/继续任务：

```text
cf-task-start runtime-kernel TASK-002
```

查看任务状态：

```text
cf-task-status runtime-kernel
```

不要手工维护 `.active-task.json`；`cf-task-start` 会负责 Context refresh、Start Gate、active marker、Task Session、RED/GREEN 和验收证据。
