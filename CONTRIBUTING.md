# 贡献指南（Contributing）

感谢你考虑为 Fluxion Harness 做贡献。Fluxion 是一个无状态、插件化的 Agent Harness，采用
「问题驱动 + ADR + 严格验收」的开发方式。请在提交贡献前阅读本指南与仓库根目录的
`AGENTS.md`（不可违反的架构规则）。

## 行为准则

请遵守 `CODE_OF_CONDUCT.md`。任何形式的骚扰、歧视与不尊重他人都不被容忍。

## 开始之前

1. **先看事实源**：`docs/foundation/02-核心需求.md`（REQ-*）、`docs/foundation/03-架构原则.md`（ARCH-*）与
   `docs/architecture/总体架构.md` 定义了不可违反的架构基线。
2. **问题驱动**：核心架构变更必须能指出它解决的 REQ/ARCH 或历史 Pxx driver，记录 ADR（`docs/adr/`），
   并给出验证策略。禁止"为了模式而模式"的 cargo cult。
3. **范围对齐**：功能变更先经过 code-flow 的 TASK 拆分（`cf-task-start` 等命令），
   不要在编码时自行扩大范围。

## 开发环境

- 后端：Python 3.12 + uv（`uv sync --extra dev`）。
- 前端：Node 22 + pnpm 10（`pnpm install --frozen-lockfile`）。
- 本地跑通：`fluxion serve --dev`（组合 Console API + Channel API + Runtime + PostgreSQL + 前端静态资源；前置：本地 PG 常驻 + `FLUXION_SECRET_MASTER_KEY`）。

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

### Playwright E2E 分组

日常开发默认只运行功能与错误路径场景，不执行耗时且对环境敏感的性能/A11Y NFR：

```bash
# 默认：6 个功能与错误路径场景
pnpm test:e2e

# 仅运行性能与 A11Y NFR
pnpm test:e2e:nfr

# 单独运行全部 Playwright E2E
pnpm test:e2e:all
```

排查具体场景时可以进一步限定文件或用例标题：

```bash
# 单个 spec 文件
pnpm exec playwright test frontend/e2e/agent-golden-path.spec.ts

# 标题匹配的单个或一组用例
pnpm exec playwright test --grep "S-P13-06"
```

NFR 套件建议在空闲机器或独立 CI 阶段运行，避免本地负载导致性能阈值抖动。

### E2E 本地排障（实机经验）

- 测的是预构建包：Playwright 测的是 `frontend/apps/console/dist`，改完 console 先
  `pnpm --filter @fluxion/console build` 再跑，否则断言的是旧包。
- 共享 dev 库种子多为非幂等：重跑遇到 `31009 resource version already exists` /
  `33009` 先清理对应资源残留，不要为迁就重跑去改种子逻辑。
- 宿主端口占用：跑之前确认无残留 `fluxion serve` 进程占用目标端口
  （`lsof -nP -iTCP:<port> -sTCP:LISTEN`），playwright webServer 异常退出会遗留进程，
  导致请求打到旧服务上（曾出现宿主 8000 返回 `mode: dev` 而容器内是 `production`）。
- Compose topology live-fire（S-01 类）：外部 PG 用独立容器提供（compose 内禁自带），
  用临时 override 文件把三角色容器接入外部网段（不改 `deploy/docker/docker-compose.yml`
  本体），先跑 `scripts/init_db.py` 建表；生产 `publish` 默认强制 Release Gate，
  seeding 如被 gate 拦住属于预期行为，与拓扑验证无关。

## 代码规范要点

- 单文件原则上不超过 500 行，单函数原则上不超过 50 行。
- 禁止静默吞异常；禁止硬编码 Secret、非参数化 SQL、循环内无界网络调用。
- TypeScript 禁止 `any` 与滥用 `@ts-ignore`；Python 公共函数/类必须有类型注解。
- 外部调用必须定义 timeout 与失败策略。

## 提 Issue / PR

- Issue 请描述问题、复现步骤、预期与实际行为，并标注相关 design driver 或 TASK。
- PR 请关联 Issue/TASK，说明改了什么、为什么、如何验证（测试证据）。
- 涉及核心 Contract 的变更必须先建 ADR，禁止在 PR 中直接改架构规则。
