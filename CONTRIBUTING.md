# 贡献指南（Contributing）

感谢你考虑为 Fluxion Harness 做贡献。Fluxion 是一个无状态、插件化的 Agent Harness，采用
「问题驱动 + ADR + 严格验收」的开发方式。请在提交贡献前阅读本指南与仓库根目录的
`AGENTS.md`（不可违反的架构规则）。

## 行为准则

请遵守 `CODE_OF_CONDUCT.md`。任何形式的骚扰、歧视与不尊重他人都不被容忍。

## 开始之前

1. **先看事实源**：`docs/architecture/fluxion-architecture-baseline-v1.md` 与
   `docs/problems/design-drivers.md` 定义了不可违反的架构基线。
2. **问题驱动**：核心架构变更必须能指出它解决的 design driver，记录 ADR（`docs/adr/`），
   并给出验证策略。禁止"为了模式而模式"的 cargo cult。
3. **范围对齐**：功能变更先经过 code-flow 的 TASK 拆分（`cf-task-start` 等命令），
   不要在编码时自行扩大范围。

## 开发环境

- 后端：Python 3.12 + uv（`uv sync --extra dev`）。
- 前端：Node 22 + pnpm 10（`pnpm install --frozen-lockfile`）。
- 本地跑通：`fluxion serve --dev`（组合 Console API + Channel API + Runtime + SQLite + 前端静态资源）。

## 提交规范

- 提交信息与代码注释、文档能用中文时优先中文；代码标识符保持英文。
- 一个 commit 做一件事，说明动机（为什么）而非只描述（做了什么）。
- 所有变更必须带测试；P0/P1 场景自动化率不低于 95%。

## 质量门槛（PR 合并前必须全绿）

```bash
# 后端
uv run ruff check backend/src backend/tests
uv run mypy backend/src
uv run python -m pytest backend/tests -q

# 前端
pnpm --filter @fluxion/shared typecheck
pnpm --filter @fluxion/console typecheck && pnpm --filter @fluxion/console lint && pnpm --filter @fluxion/console test
pnpm --filter @fluxion/chat typecheck && pnpm --filter @fluxion/chat lint && pnpm --filter @fluxion/chat test
node frontend/scripts/check-no-inmemory.mjs
```

CI（`.github/workflows/`）会在 push/PR 时自动运行这些门槛。

## 代码规范要点

- 单文件原则上不超过 500 行，单函数原则上不超过 50 行。
- 禁止静默吞异常；禁止硬编码 Secret、非参数化 SQL、循环内无界网络调用。
- TypeScript 禁止 `any` 与滥用 `@ts-ignore`；Python 公共函数/类必须有类型注解。
- 外部调用必须定义 timeout 与失败策略。

## 提 Issue / PR

- Issue 请描述问题、复现步骤、预期与实际行为，并标注相关 design driver 或 TASK。
- PR 请关联 Issue/TASK，说明改了什么、为什么、如何验证（测试证据）。
- 涉及核心 Contract 的变更必须先建 ADR，禁止在 PR 中直接改架构规则。
