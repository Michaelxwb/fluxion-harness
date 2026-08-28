# Fluxion 文档基线

> 版本：2026-08-28 重构基线
> 状态：建议评审版
> 语言：中文为主，代码符号/协议/标准名保留英文

## 1. 文档权威关系

本文档集不再把历史 Pxx、旧 ADR、PRD、整改 Roadmap、Task 和当前代码放在同一权威层级。

唯一解释方向：

`原始问题证据 → 当前核心需求 → 架构原则 → 总体架构 → ADR → 详细设计 → 开发 Gate/Task → Code`

下层与上层冲突时，优先整改下层。当前代码"已经实现"不构成保留错误架构语义的理由。

## 2. 当前代码事实

当前 main 已经具备大量正确基础：AgentDefinition、RuntimeProfile、ExecutionSnapshot、Registry、SQL Session Memory、PlatformUser/Profile/Preference、MCP Binding、WorkflowEngine Contract、Plugin Contract、A2A、OTel/Trace 基础等。

同时存在必须在继续扩功能前收口的偏差：

- Tool 用户授权维度在执行侧退化为 `user_tools = agent_tools`；
- UserDomainService 的 Capability Grant 只允许 Skill/MCP，拒绝 Tool；
- AgentDefinition 使用 `CapabilityBinding` 命名，容易把 Agent Allowlist 与用户 Binding 混淆；
- Scheduler 仍以 Runtime 本地 `_tasks` 表示任务事实；
- Trace/Approval/Eval 等存在 InMemory 默认/实现，生产边界必须显式收紧；
- Stateless 主要是"按设计成立"，还缺真实多 Pod Gate 证明。

## 3. 阅读顺序

1. `foundation/01-问题与目标.md`
2. `foundation/02-核心需求.md`
3. `foundation/03-架构原则.md`
4. `design/08-用户旅程与体验设计.md`（用户旅程：功能的正当性来源与验收视角）
5. `architecture/总体架构.md`
6. `design/01-Runtime与Execution设计.md`
7. 其余领域设计
8. `development/架构验收Gate.md`
9. `migration/当前代码偏差与迁移.md`

## 4. 历史文档

旧 `docs/problems/design-drivers.md`、ADR-001～013、Runtime V1.7、Console V1.6、V2.2 PRD/整改路线图已随基线切换移除（git 历史可查），不再作为新实现的事实源。
