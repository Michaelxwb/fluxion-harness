# Fluxion Harness

> 面向企业内网的智能服务交付平台。把业务 SOP 写成 Skill，由 Agent 在受控、可审计、可扩展的运行时中执行。

Fluxion Harness 不是通用聊天机器人框架，而是企业智能服务的交付底座：业务能力沉淀为可版本化的 Skill，Agent 负责理解意图，Runtime 负责可靠执行，Console 负责配置、授权与审计，IM Gateway 负责把结果送回用户。

适合需要私有化、多租户、可审计的企业内部 Agent 场景，例如业务 SOP 执行、跨系统操作、定时/批量任务和知识型服务。

**Skill-first · 私有化部署 · 多租户 · 生产级运行时**

## 为什么选择 Fluxion Harness

### 1. 业务能力沉淀为 Skill，而不是提示词

每个 Skill 是一个标准包：`SKILL.md` 定义能力与触发条件，`scripts/` 放确定性逻辑，`references/` 放长文档。Skill 可以独立开发、测试、导入和升级，不依赖某个模型或某段对话。

业务逻辑因此可复用、可审查、可交付，而不是散落在提示词和临时脚本里。

### 2. Runtime 无状态，扩容不绑定 Agent

Agent、Conversation、Memory、Snapshot、Artifact 等权威状态全部外置。任意 Runtime Pod 都可以执行任意 Agent；同一个用户连续两轮消息可以由不同 Pod 处理。

扩容只是增加副本，不需要迁移会话，也不需要把某个 Agent 固定到某台机器。

### 3. 执行方式由确定性路由决定，而不是模型自由发挥

用户只表达一个业务意图，Agent 负责提取意图和参数。`ExecutionRouter` 再根据 Skill 元数据和确定性规则选择：

- `SYNC`：当前请求内完成；
- `ASYNC`：进入持久化后台任务；
- `AUTO`：按确定性规则判断。

不让模型直接决定“要不要后台执行”，避免同一业务时快时慢、时成功时丢任务。

### 4. Skill 制品不可变，版本可追溯

每次导入生成不可变 Artifact，保存 checksum、版本、文件清单和 `SKILL.md` 快照。Runtime/Worker 使用本地缓存执行，启动前校验 checksum，原子切换 `READY`，不直接从共享存储执行脚本。

同一份制品可以在任意 Pod 复现；升级只新增版本，不覆盖历史。

### 5. 企业平台接入不重复造轮子

`PlatformAdapter + SessionManager + Egress Boundary` 统一处理企业平台的登录、Session、请求签名和出网边界。Console 根据 Adapter 声明的 schema 动态生成配置表单；新增平台如果复用已有协议，不需要改前端。

Skill 里不保存平台密钥，也不需要每个 Skill 自己实现登录和 CSRF。

### 6. 授权边界内置，不靠页面隐藏

平台采用两层授权：

```text
User -> Agent        AgentAccessGrant
Agent -> Skill/MCP   AgentSkillBinding / AgentMcpBinding
Skill/MCP 用户范围    ALL / SELECTED + 资源级 Grant
```

Runtime 在构建 Prompt 和 ToolRegistry 之前完成交集过滤：未授权、已禁用、已删除的资源不会进入模型上下文，也不会出现在工具列表里。

### 7. 长耗时任务是一等公民

后台任务、定时任务、批量并行、父/子任务、Fan-in 聚合和最终结果投递都在平台内完成。任务有租约、心跳、deadline、取消、重试和幂等键；进程重启后可以继续领取或回收。

用户不需要守着页面等结果，业务也不需要为每个长任务另建一套调度系统。

### 8. 生产可观测、可审计、可验证

- 配置变更、授权变更、运行与工具调用都有审计记录；
- 统一 Trace ID / Request ID，日志按服务落盘；
- 关键路径有指标与容量保护；
- 错误码、中英文文案、配置项、前端调用都有静态校验；
- 2,800+ 快速单元/契约测试，另有 console/runtime/worker 分片、验收测试和浏览器 E2E。

## 核心能力

| 能力 | 说明 |
|---|---|
| Agent | 指令、模型、Skill、MCP、用户授权与 IM 通道的统一编排 |
| Skill | `SKILL.md` + Python/JavaScript 脚本，支持版本、用户范围和运行时缓存 |
| MCP | 工具发现、目录快照、统一进入 ToolRegistry |
| Console | 12 个管理模块，覆盖配置、授权、审计、任务与系统设置 |
| IM / WebChat | 企业微信与 WebChat 接入，一个 Agent 可绑定多个通道账号 |
| 后台任务 / 定时任务 | 持久化队列、租约、重试、取消、父子任务与最终投递 |
| 记忆与学习 | 用户记忆、候选确认、经验回放与激活 |
| 可观测性 | 审计、日志、Trace、指标与容量治理 |

## 服务组成

| 服务 | 职责 |
|---|---|
| `console-platform` | 管理控制台与配置/授权/审计 API |
| `agent-runtime` | 无状态 Agent 执行、SSE、工具调用与上下文组装 |
| `agent-worker` | 后台任务、定时调度与长耗时执行 |
| `im-gateway` | IM/WebChat 入站、会话与消息投递 |
| `im-connector` | 企业微信等长连接通道，按需独立部署 |

外部依赖：PostgreSQL、Redis、Artifact Store（生产为 NFS-backed RWX PVC）。

## 快速开始

要求：Python 3.12+、Node 20+、uv、PostgreSQL、Redis。

```bash
cp .env.example .env          # 填写 DATABASE_URL / REDIS_URL / ARTIFACT_ROOT

uv sync --all-packages --extra dev

# 首次初始化：建库/迁移/创建 Console 管理员
uv run python migrations/bootstrap_db.py
uv run python migrations/db_migrate.py
uv run muad-console-admin create-admin --username admin --tenant default

# 启动后端与前端
scripts/dev.sh
```

打开 `http://localhost:5173`，使用刚创建的 Console 账号登录。

数据库连接串在 `migrations/alembic.ini`，应用连接串在 `.env`。生产部署必须先迁移到 head，再滚动服务。更多说明见 [docs/README.md](docs/README.md)、[build/README.md](build/README.md) 和 [deploy/k8s/README.md](deploy/k8s/README.md)。

## 质量与工程

- 分层测试：fast / console / runtime / worker / acceptance / browser E2E，CI 分片并行；
- 契约测试覆盖 API 封套、错误码、前端调用、配置盘点与关键业务不变量；
- 迁移到 head 才允许服务启动，避免“代码已升级、库没升级”；
- 业务代码只抛错误码，中英文文案统一由消息目录生成；
- Spec 驱动的设计/任务/验收流程，关键规则有可执行 verifier。

## 文档

- [详细设计索引](docs/README.md)
- [总体架构与部署](docs/01-总体架构与部署详细设计.md)
- [Agent Runtime](docs/04-Agent-Runtime详细设计.md)
- [Skill SDK 与 Egress Boundary](docs/06-Skill-SDK与Egress-Boundary详细设计.md)
- [用户范围与能力授权](docs/14-用户范围与能力授权模型详细说明.md)

## 当前阶段

Phase 1 已覆盖 Agent、Skill、MCP、Console、IM、后台任务、定时任务、记忆、审计与容量治理。后续能力按业务场景持续扩展。
