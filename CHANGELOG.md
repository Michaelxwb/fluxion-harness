# 变更日志（Changelog）

本项目遵循「无状态、插件化、Resource 驱动」的架构基线（`docs/architecture/总体架构.md`）。
本文件记录面向使用者的重要变更。

## [0.1.0] - 2026-08

### 新增（第一阶段：Agent Runtime + Console + Web Chat）

- **Agent Runtime**：Microkernel + Plugin + 类型化 Hook；Agent Loop、模型 Provider、MCP、Skill、Tool、Memory、Sandbox、Workflow Adapter。
- **Resource Registry**：版本化 Resource（RuntimeProfile / Skill / MCP / Plugin / Workflow / Policy）；Dev SQLite / Prod PostgreSQL 共用同一 RegistryStore 契约与 Contract Test。
- **ExecutionSnapshot**：一次执行固定资源版本，发布热更新不影响进行中的执行。
- **Console / Control Plane**：管理版本化资源、发布/回滚、Bindings、Policy、Eval、Audit、Runs/Trace、P1 视图。
- **Web Chat Channel**：正式用户 Channel；access link 绑定；未绑定用户受限。
- **内置基础工具**：time / calc / http / search_files / file / run_command。
- **Sandbox Backend**：Linux bubblewrap、macOS sandbox-exec，dev 降级非生产。
- **CLI**：`fluxion run` / `fluxion serve` / `fluxion validate` / `fluxion plugins list`。
- **安全**：SecretRef / SecretStore（AES-256-GCM）、tenant 全链路隔离、审计与统一响应 envelope。

### 工程质量

- SQLite/PostgreSQL 双库 Contract 测试；P0/P1 自动化验收。
- 架构依赖门禁（Kernel/Runtime AST 检查）。
- 前端 React 19 + Semi Design，Console/Chat 独立构建。

## 计划中（Roadmap）

- 独立 Python/TypeScript SDK（现阶段仅 CLI）。
- Workflow Engine / DSL（业务接入层，不在开源 V1 范围）。
- 更多 IM Channel Adapter（飞书 / QQ / 企微）。
- 生产 Canary / 多 Pod 压测 / 独立部署演练的自动化。
