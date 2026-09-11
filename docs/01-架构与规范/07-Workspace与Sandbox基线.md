# Workspace 与 Sandbox 设计基线 V1.9.1

## 1. 边界

Workspace/Sandbox 只为受控执行提供临时文件和进程隔离，不是用户长期业务状态 SoT。

## 2. Skill

Skill Python 在受控 Runtime/Sandbox 中运行时：

- 只读已展开的 Artifact 目录；
- 运行时临时目录隔离；
- 不允许读取 Host 任意路径；
- Secret 不写文件包；
- 需要持久输出用 `ctx.artifact.write`；
- 外部系统调用优先走 `ctx.capability.call`。

## 3. Sandbox Capability

Sandbox Implementation 需配置：

- executable allowlist；
- command/template；
- working directory policy；
- env allowlist；
- CPU/memory/time limit；
- stdout/stderr size limit；
- network policy（默认最小化）；
- output mapping。

禁止用户在 Console 输入任意 shell command 直接执行。

## 4. 文件安全

Skill import/extract：

- 拒绝绝对路径；
- 拒绝 `../` 路径穿越；
- 拒绝 symlink 越界；
- 限制压缩包展开总大小/文件数量；
- 允许扩展名白名单；
- checksum；
- 临时目录失败清理。

## 5. 生命周期

临时 Workspace 随 turn/execution/skill invocation 创建并可回收。Artifact 上传完成后本地临时文件不是权威事实。

## 6. 可观测性

记录 sandbox invocation id、skill/artifact checksum、capability、duration、exit code、resource limit event；不记录 Secret env 值。

## 7. 开发环境

Skill SDK 本地 Mock 不要求 Sandbox。Dev Gateway 联调走远端开发 Runtime，不要求开发者复制生产 Sandbox 到本地。
