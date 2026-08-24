# Shared Specs Navigation Map

> 跨项目的共享模板、设计材料与跨语言契约占位。

## Purpose

共享模板供 `cf-task:align` 和 `cf-task:prd` 命令使用，为文档生成提供规范约束。运行时跨语言契约的仓库边界是 `shared/contracts/`，当前只有占位文件，具体 Schema/OpenAPI/Event Contract 会在相关任务中落地。

## Key Files

| 文件 | 用途 |
|------|------|
| `shared/contracts/.gitkeep` | 跨语言 Schema / OpenAPI / Event Contract 占位 |
| `.code-flow/specs/shared/prd-template.md` | PRD 模板 |
| `.code-flow/specs/shared/design/design-lite.md` | 轻量设计简报模板 |
| `.code-flow/specs/shared/design/design-full.md` | 完整设计文档模板 |
| `.code-flow/specs/shared/design/design-frontend.md` | 前端设计模板 |

## Templates

### PRD Templates

| 文件 | 用途 | 适用场景 |
|------|------|---------|
| `prd-template.md` | 产品需求文档 | 需求早期阶段，在设计之前 |

### Design Templates

| 文件 | 用途 | 适用场景 |
|------|------|---------|
| `design/design-lite.md` | 轻量设计简报 | 功能开发/CLI/Bug修复/小型重构 |
| `design/design-full.md` | 完整设计文档 | 跨系统集成/性能优化/架构演进/中大型功能 |
| `design/design-frontend.md` | 前端设计文档 | Console/Chat/Semi Design 前端任务 |

> 两档模板均覆盖：接口设计（API/CLI/函数三形态）、性能与容量设计、可执行验收（场景即 TC）。design-full 额外含方案选型 ADR 决策记录与 §6 需求追溯矩阵。

## Workflow

```
需求 → cf-task:prd → PRD (.prd.md)
       ↓ （align 读取 .prd.md 派生）
       → cf-task:align → 设计 (.design.md)
       ↓ （plan 读取 .design.md 拆解）
       → cf-task:plan → 任务
```

## Selection Guide

```
需求阶段（还未明确用户与场景）：
  → cf-task:prd 生成 PRD

设计阶段（已有 PRD 或已明确做什么）：
  → cf-task:align 生成设计简报
    - 输入 .prd.md → 派生模式（继承目标/用户/功能/范围）
    - 输入文本 → 新建模式（从零对话）

复杂度判断：
  简单功能/脚本/Bugfix → design-lite.md
  复杂系统/跨模块/性能优化 → design-full.md
```

## Usage

1. `cf-task:prd` 命令引用 `prd-template.md` 生成 PRD
2. `cf-task:align` 命令引用 `design/design-lite.md` 或 `design/design-full.md` 生成设计文档
3. PRD 的 US/FEAT ID 被 design 的功能清单"来源"列引用，形成追溯链
4. 性能敏感需求：align 在出技术方案时即按最优性能设计，落点 design-lite §3.4 / design-full §3.5，并在 §6 矩阵闭合 US→FEAT→API→TC
5. 新增跨语言契约：放 `shared/contracts/`，并同步相关后端/前端消费测试。
