# Console 前端模块设计索引 — V1.11 模块分档拆分版

前端不再使用一篇“大一统 Console 设计”。每个独立产品页面/旅程模块使用一份 `design-frontend.md`，公共 Shell/列表规范单独成模块。

| 序号 | 模块 | 路由 | 分档 | 文档 |
|---|---|---|---|---|
| 00 | Console公共框架 | `/console/*` | Frontend | `00-Console公共框架/design-frontend.md` |
| 01 | 概览 | `/console/overview` | Frontend | `01-概览/design-frontend.md` |
| 02 | 服务管理 | `/console/services, /console/services/:id` | Frontend | `02-服务管理/design-frontend.md` |
| 03 | 智能体管理 | `/console/agents, /console/agents/:id` | Frontend | `03-智能体管理/design-frontend.md` |
| 04 | 能力管理 | `/console/capabilities` | Frontend | `04-能力管理/design-frontend.md` |
| 05 | Skill管理 | `/console/skills, /console/skills/:id` | Frontend | `05-Skill管理/design-frontend.md` |
| 06 | Knowledge规划 | `/console/knowledge` | Frontend | `06-Knowledge规划/design-frontend.md` |
| 07 | 模型管理 | `/console/models` | Frontend | `07-模型管理/design-frontend.md` |
| 08 | 项目平台管理 | `/console/project-platforms` | Frontend | `08-项目平台管理/design-frontend.md` |
| 09 | 用户管理 | `/console/users, /console/users/:id` | Frontend | `09-用户管理/design-frontend.md` |
| 10 | 执行记录 | `/console/executions, /console/executions/:id` | Frontend | `10-执行记录/design-frontend.md` |

## 事实源优先级

1. 已评审的 `90-Console交互规格.md` 与 V0.8 HTML 交互稿；
2. 本目录各页面 `design-frontend.md`；
3. 后端模块 API/DB Owner 设计；
4. 旧前端实现只能作为迁移参考，不得覆盖上述交互事实。

## 拆分边界

- Agent 的 WeCom/用户授权仍属于“智能体管理”页面，不新增 Channel 一级菜单。
- User 的项目平台认证、Agent 授权、IM 身份仍属于“用户管理”详情 Tabs，不拆成独立一级菜单。
- AsyncTask 属于“执行记录”详情，不拆独立页面。
- Knowledge 当前只有规划页。
