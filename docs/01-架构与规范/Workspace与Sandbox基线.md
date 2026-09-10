# Workspace 与 Sandbox 基线

## 1. 目标

通用 Agent 需要 `read_file/write_file/edit_file/glob/grep/shell` 等能力，但这些能力不能破坏 Agent Runtime 无状态边界。

因此统一建模为：

```text
Sandbox-backed Capability
```

## 2. 内置 Capability

```text
filesystem.read
filesystem.glob
filesystem.grep
filesystem.write
filesystem.edit
shell.execute
```

这些不是第二套 Tool 产品模型。

## 3. Workspace 边界

```text
Conversation / ServiceExecution
        ↓
    workspace_id
        ↓
 WorkspaceManager
        ↓
 SandboxExecutor
```

`workspace_id` 来自 TrustedExecutionContext。

LLM 只能提供 Workspace 内相对路径，不能提供 Host Root。

## 4. Local Sandbox

`LocalSandboxExecutor` 只允许用于开发/受信环境。

必须至少提供：

```text
tenant check
path normalization
absolute path rejection
.. traversal rejection
symlink escape protection
operation allowlist
shell disabled by default
executable allowlist
argv execution
timeout
output limit
```

这些控制不等于 Kernel/Container 级安全隔离。

## 5. Production Sandbox

生产若启用 Shell 或不可信代码执行，必须使用真正隔离的 Executor，例如：

```text
Container
Kubernetes Pod/Job
Linux Namespace
MicroVM
```

隔离范围至少考虑：

```text
filesystem
process
uid
cpu/memory/pid
network egress
syscall/profile
workspace mounts
cleanup/TTL
```

## 6. 执行路由

实时可直接执行：

```text
read
glob
grep
```

高风险/需可靠执行：

```text
shell
长时命令
高风险写操作
```

必须进入：

```text
ExecutionService
→ Worker
→ Capability Step
```

最终由 Capability Contract 元数据决定，而不是在 Agent Runtime 写死 Capability 名称。
