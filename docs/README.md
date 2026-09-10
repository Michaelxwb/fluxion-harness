# 通用智能服务执行框架设计文档

> **设计基线**：总体设计 V1.7（D01-D08 已合入正文；V1.6 原文见 git 历史）  
> **生成日期**：2026-09-10  
> **文档语言**：中文  
> **设计阶段**：总体设计已形成，进入 Framework Core 模块详细设计阶段  
> **业务接入状态**：当前只定义 Integration 边界，不包含任何具体 MSS/CRM/ERP 业务接口详细接入设计

## 1. 本目录用途

本目录是当前框架设计基线的完整 `docs/`。

设计文档按“总体设计 → 架构规范 → 模块详细设计 → 前端设计 → 追溯/验收 → 变更记录”组织。

```text
docs/
├── README.md
├── 00-总体设计/
├── 01-架构与规范/
├── 02-模块设计/
├── 03-前端设计/
├── 04-追溯与验收/
└── 05-变更记录/
```

## 2. 三类设计模板的使用原则

本轮详细设计使用三类模板：

| 模板 | 用途 | 本目录中的使用方式 |
|---|---|---|
| `design-full.md` | 跨模块、核心架构、数据/接口/可靠性要求高的模块 | Agent Runtime、Worker、Capability、Auth、Channel、Workspace 等核心模块 |
| `design-lite.md` | 简单模块、公共基础、小型重构/支撑能力 | 存储与基础设施适配、可观测性与 Web 公共基础 |
| `design-frontend.md` | 前端页面/组件/交互设计 | Console 前端模块 |

模板中的示例性能值没有被照抄。没有真实测量依据的性能、SLA、容量阈值均保留为“待定”。

## 3. 设计边界

Framework Core 只认识：

```text
User
Agent
Service
Skill
Knowledge
Capability
Channel
Conversation
Execution
Workspace
Artifact
Auth
```

不认识具体项目的：

```text
customer
device
EDR
policy_check
CRM order
ERP purchase
```

具体项目通过 `Project Integration` 接入。

## 4. 当前模块划分

### 核心领域与应用模块

1. 核心领域与发布模型
2. Platform API 与控制面
3. Agent Runtime
4. Agent Core 与 Agent Executor
5. ExecutionService
6. Worker Engine
7. Capability Runtime
8. Knowledge Runtime
9. Auth Runtime 与 AuthProvider
10. Channel Gateway
11. Workspace 与 Sandbox
12. Conversation 与 User Memory
13. Project Integration 与 Registry

### 公共基础模块

14. 存储与基础设施适配
15. 可观测性与 Web 公共基础

### 前端模块

16. Console 前端

## 5. Production 部署单元

逻辑模块不等于独立微服务。

当前只有四个常驻应用镜像：

```text
platform-api
agent-runtime
worker
channel-gateway
```

其他模块以 Framework Package、Provider、Adapter 的方式被四个进程复用。

## 6. 强制工程约束

### 后端

```text
统一 Response Envelope
统一 Exception Pipeline
统一 request_id
统一结构化 JSON 日志
统一敏感字段脱敏
OpenTelemetry
```

### 前端

```text
Semi Design
StandardListPage
左上：新增/主要操作
右上：过滤/筛选/搜索
中部：Table
右下：Pagination
详情 Drawer 默认只读
```

### 可靠执行

```text
PostgreSQL = Execution SoT
Redis ≠ SoT
Worker claim/lease/reclaim
Long wait = next_run_at
Idempotency
Crash recovery
```

### 工具与 Sandbox

```text
read_file/write_file/edit_file/glob/grep/shell
=
Sandbox-backed Capability

不新增 Tool Runtime
不允许 Agent Runtime 直接 Host Shell
```

## 7. 开发前推荐阅读顺序

```text
00-总体设计
→
01-架构与规范/架构基线
→
04-追溯与验收/模块划分与依赖矩阵
→
对应的 02-模块设计
→
03-前端设计
→
01-架构与规范/后端工程规范或前端工程规范
```

## 8. 后续业务接入文档

当开始 MSS 等真实业务接入时，新增：

```text
docs/
└── 06-业务接入/
    └── mss/
        ├── 接入总体说明.md
        ├── Capability映射.md
        ├── AuthProvider设计.md
        ├── Service详细设计.md
        └── E2E验收.md
```

业务接入文档可以新增，但不得把项目专属字段回写到 Framework Core 总设和核心模块模型中。
