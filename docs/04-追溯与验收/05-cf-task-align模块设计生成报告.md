# cf-task:align 模块设计生成报告

> 日期：2026-09-11  
> 输出版本：V1.11 模块分档拆分版

## 1. 输入基线

- 完整总体设计：`00-总体设计/01-通用智能服务执行框架-V1.8-完整总体设计说明书.md`
- 完整 Playbook：`00-总体设计/02-用户场景与Playbook旅程设计-V6-完整版.md`
- 交互稿：`03-前端设计/fluxion-console-interaction-prototype-v0.8-final.html`
- 用户提供模板：`design-full.md` / `design-lite.md` / `design-frontend.md`
- `cf-task:align` 流程定义：`align.md`

## 2. 模板选择

| 模块 | 模板 | 理由 |
|---|---|---|
| 核心领域与发布模型 | design-full | 复杂后端/架构模块，涉及 DB/API/跨模块契约 |
| Platform API 与控制面 | design-full | 复杂后端/架构模块，涉及 DB/API/跨模块契约 |
| Agent Runtime | design-full | 复杂后端/架构模块，涉及 DB/API/跨模块契约 |
| Agent Core 与 Agent Executor | design-full | 复杂后端/架构模块，涉及 DB/API/跨模块契约 |
| Service 与 Execution | design-full | 复杂后端/架构模块，涉及 DB/API/跨模块契约 |
| Worker Engine | design-full | 复杂后端/架构模块，涉及 DB/API/跨模块契约 |
| Capability Runtime | design-full | 复杂后端/架构模块，涉及 DB/API/跨模块契约 |
| Skill Runtime | design-full | 复杂后端/架构模块，涉及 DB/API/跨模块契约 |
| Auth 与项目平台 | design-full | 复杂后端/架构模块，涉及 DB/API/跨模块契约 |
| Channel Gateway | design-full | 复杂后端/架构模块，涉及 DB/API/跨模块契约 |
| Conversation 与 User Memory | design-full | 复杂后端/架构模块，涉及 DB/API/跨模块契约 |
| Project Integration 与 Registry | design-full | 复杂后端/架构模块，涉及 DB/API/跨模块契约 |
| Workspace 与 Sandbox | design-full | 复杂后端/架构模块，涉及 DB/API/跨模块契约 |
| 存储与基础设施适配 | design-full | 复杂后端/架构模块，涉及 DB/API/跨模块契约 |
| 可观测性与 Web 公共基础 | design-full | 复杂后端/架构模块，涉及 DB/API/跨模块契约 |
| Skill SDK与离线开发 | design-full | 复杂后端/架构模块，涉及 DB/API/跨模块契约 |
| 用户与 Agent 授权 | design-full | 复杂后端/架构模块，涉及 DB/API/跨模块契约 |
| 模型配置与调用 | design-full | 复杂后端/架构模块，涉及 DB/API/跨模块契约 |
| Knowledge规划 | design-lite | 明确规划中，当前不应冻结生产 DB/API |
| Console前端 | design-frontend | 页面/路由/组件/状态/UI 与后端 services 映射 |

## 3. 本轮特别补齐

1. 每个 Full 模块 §3.3 细化到字段类型、NULL、默认值、索引、唯一/CHECK/不可变约束和 ER；无 Owner 表时明确说明为什么不建表。
2. 每个 Full 模块 §3.4 对**每一个** HTTP/Library/CLI 接口写请求、响应、错误码、处理逻辑和安全/幂等约束。
3. 新增独立 `用户与 Agent 授权`、`模型配置与调用` 模块，避免 Console 两个核心菜单被其他文档顺带描述。
4. 新增全局 DB 所有权索引和接口所有权索引，防止多个模块重复实现同一事实。
5. Skill 模型完全按最新结论：IDE Python + SDK；Console 导入/只读；`skill.yaml` 是 Capability dependency SoT。
6. Knowledge 保持规划，不因“详细设计”要求强行造表/API。

## 4. cf-task:align 硬门禁状态

`align.md` 要求 Step 2 扫描真实代码库并在 design 阶段刷新/绑定 `spec-context.yml`。本轮输入是设计资料与模板，**没有提供当前 repo 文件树、spec-context.yml 和 cf_spec_context.py 的执行环境**，因此：

- 已执行：模板选择、需求/边界、数据模型、接口、性能/质量、S/E/B 验收、追溯矩阵；
- 未声称执行：真实代码扫描、`spec-context refresh/catalog/bind`；
- 文档状态保持“待仓库 Spec Context 绑定”，落码前必须在仓库补这一 Gate。
