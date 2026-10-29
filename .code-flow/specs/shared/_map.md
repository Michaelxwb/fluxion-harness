# Shared Specs Navigation Map

> 跨项目的共享模板和规范

## Purpose

共享模板供 `cf-task:align` 和 `cf-task:prd` 命令使用，为文档生成提供规范约束。

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
| `design/design-frontend.md` | 前端设计简报 | 前端侧与全栈需求的前端侧（产出 `<需求>.frontend.design.md`） |

> 两档设计模板都含接口设计、性能与容量、可执行验收（场景即 TC）；design-full 另含 ADR 与 §6 追溯矩阵，design-frontend 专于前端（页面/组件/状态/样式/可访问性 + Spec Compliance Matrix）。

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
  涉前端（页面/组件/交互/UI 状态）→ design-frontend.md（全栈需求的前端侧同样用它）
```

## Usage

1. 各命令自行读取上表模板；PRD 的 US/FEAT ID 被 design 的功能清单「来源」列引用，形成追溯链。
2. 性能敏感需求：align 在技术方案阶段即按最优性能设计（落点 design-lite §3.4 / design-full §3.5），并在 §6 矩阵闭合 US→FEAT→API→TC。
