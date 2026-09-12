# Console 前端模块设计索引 — V1.14.2

前端不再使用一篇“大一统 Console 设计”。每个独立产品页面/旅程模块使用一份 `design-frontend.md`，公共 Shell/列表规范单独成模块。

> **本 README 顶部同步（V1.14.2 第六轮 Review 收敛）**：删 `archive/` 与「原型被取代范围声明」整节（改为两条事实源规则）；删 `06-Knowledge规划/` 占位页与菜单行；服务管理删 `/:id` 只读详情路由（详情即编辑页只读态）；`90-§13` 映射表删除（改为指向 `FE-*/§3.4-3.5` + 后端 Owner）；Agent 绑定合并为一次 `PATCH .../bindings`；授权写入口唯一化（删 `USR-API-08/08R`）；项目平台整接口仅 Admin；投递侧状态改名 `RETRY_PENDING`；`StandardListQuery` 收敛为 4 参数 + 各页领域筛选白名单。

| 序号 | 模块 | 路由 | 分档 | 文档 |
|---|---|---|---|---|
| 00 | Console公共框架 | `/console/*` | Frontend | `00-Console公共框架/design-frontend.md` |
| 01 | 概览 | `/console/overview` | Frontend | `01-概览/design-frontend.md` |
| 02 | 服务管理 | `/console/services, /console/services/new, /console/services/:id/edit` | Frontend | `02-服务管理/design-frontend.md` |
| 03 | 智能体管理 | `/console/agents, /console/agents/:id` | Frontend | `03-智能体管理/design-frontend.md` |
| 04 | 能力管理 | `/console/capabilities` | Frontend | `04-能力管理/design-frontend.md` |
| 05 | Skill管理 | `/console/skills, /console/skills/:id` | Frontend | `05-Skill管理/design-frontend.md` |
| 07 | 模型管理 | `/console/models` | Frontend | `07-模型管理/design-frontend.md` |
| 08 | 项目平台管理 | `/console/project-platforms` | Frontend | `08-项目平台管理/design-frontend.md` |
| 09 | 用户管理 | `/console/users, /console/users/:id` | Frontend | `09-用户管理/design-frontend.md` |
| 10 | 执行记录 | `/console/executions, /console/executions/:id` | Frontend | `10-执行记录/design-frontend.md` |
| 11 | 审计查询 | `/console/audit-logs` | Frontend | `11-审计查询/design-frontend.md` |

> **Knowledge 是规划项**（ADR-022）：V1 **没有** Console 菜单/路由/页面（原 `06-Knowledge规划/` 占位页已于 V1.14.2 删除）。需要建设时整章新增（菜单 + 路由 + 页面 + API/DB 契约一起），不使用占位页表达。

## 2026-09-13 第五轮契约同步（D8~D15）

后端契约（模块 05/06/07/09/12/18 与 ADR-062..067）本轮已完成裁决修正，前端设计同步如下 9 条：

| # | 前端落点 | 同步内容 |
|---|---|---|
| 1 | `04-能力管理/design-frontend.md`、`10-执行记录/design-frontend.md` | 执行源三态 `{FORMAL, TEST, CAPABILITY_TEST}`：能力测试产生 `execution_source=CAPABILITY_TEST` 的执行（有 `execution_id`），仅能力测试面板可见、不进默认执行列表；产物按其 `execution_id` 走 `EXE-API-06` 下载 |
| 2 | `04-能力管理/design-frontend.md` | `auth_mode` 是 `implementation` **顶层必填字段**（默认 `NONE`，三值 `USER_PLATFORM`/`SHARED_SECRET`/`NONE`），从 PLATFORM_SERVICE 的 `config` 字段行删除，`config` 内出现一律 400 `CAPABILITY_IMPLEMENTATION_INVALID`；`USER_PLATFORM` 必填项目平台、`SHARED_SECRET` 必填共享 Secret 引用 |
| 3 | `10-执行记录/design-frontend.md`、`90-Console交互规格.md` | 普通取消与人工决策分开映射（ADR-065）：运行态走 `EXE-API-03`（Console 必须调用），仅 `status=WAITING_HUMAN` 走 `EXE-API-05`（decision=RESUME/CANCEL）；对 RUNNING 调 `EXE-API-05` 得 409 `EXECUTION_NOT_WAITING_HUMAN` |
| 4 | `10-执行记录/design-frontend.md`、`90-Console交互规格.md` | Builder 执行可见范围 = **并集**（ADR-052/ADR-067），删除全部交集表述，与 `RULE-SVC-09`/`EXE-API-01` 一致 |
| 5 | `10-执行记录/design-frontend.md`、`90-Console交互规格.md` | 投递状态按逻辑消息取有效尝试聚合（ADR-066）：重投成功后收敛 `DELIVERED`（保留历史失败行），补 `DELIVERY_IN_FLIGHT`(409) |
| 6 | `03-智能体管理/design-frontend.md` | 授权编辑改为**单条操作**（ADR-064）：保留「全量加载 + 预勾选 + 差异确认」，提交改为逐条 `grants` / `grants/{grant_id}/revoke`，删除乐观锁/409 覆盖措辞，补授权读侧有效状态 |
| 7 | `02-服务管理/design-frontend.md` | 步骤表单新增**执行模式**（`execution_mode`，`SYNC` 默认 / `ASYNC`）与 ASYNC 专属的对账时限 / 轮询上限（ADR-062），并写死异步能力的合取校验 |
| 8 | `02-服务管理/design-frontend.md`、`90-Console交互规格.md` | 测试用户链路：默认取 `GET /api/v1/auth/me` 的 `user_id`（`AUTH-API-01`），候选取 `GET /api/v1/services/{service_id}/test-user-candidates`（`SVC-API-11`），`USR-API-01` 不得用作候选（ADR-067/D14） |
| 9 | `02-服务管理/design-frontend.md`、`90-Console交互规格.md` | 范围类型元数据来源改为 `GET /api/v1/meta/resource-scope-types`（`INT-API-01`，Owner=模块 12），空集合返回 `items: []` 且不渲染控件（ADR-067/D2） |

## 事实源规则（唯一，不再使用"取代声明"制度）

```text
跨页范式与交互规则：本 README + 90-Console交互规格.md + 00-Console公共框架
单页字段 / API / 错误码：该页 design-frontend.md 的 §3.4/§3.5 + 后端 Owner 模块 §3.4
冲突必须回写（以 Owner 为准），不允许"以某处为准但不改另一处"
```

V0.8 HTML 原型**已删除**（V1.14.2）：它冻结于 ADR-015/ADR-021 等裁决之前，作为"参考形态"长期留在仓库只会持续产出与现行契约冲突的条目（前四轮 Review 反复围绕它产生"取代声明"）。原型承载过的所有决策已分别落入 `90-Console交互规格.md`、各页 `design-frontend.md` 与后端 Owner 模块；此后不再维护任何"原型 vs 设计"的对照表——需要新形态时直接改契约。

## 显式后置（暂不建设，D7）

以下两项在本轮 Review 中确认**不做**，避免后续轮次重复发现；不在任何页面设计中出现入口、按钮或路由：

1. **END_USER 自助重试**：IM 命令集不含 `/retry`，V1 不新增该命令；失败执行的重试只由 Admin 在 Console 发起（`EXE-API-04`）。
2. **Admin 查看「来源会话消息」入口**：`EXE-API-02` 虽返回 `conversation_id`，Console **不提供**会话消息追溯视图（与 ADR-045「Console 不提供会话管理」一致，不引用 `CONV-API-01..04`）。

两项的共同前提是 Playbook U02/A04 均未要求该能力；若后续确需，需先立产品需求再改本清单。

## 拆分边界

- Agent 的 WeCom/用户授权仍属于“智能体管理”页面，不新增 Channel 一级菜单。
- User 的项目平台认证、Agent 授权、IM 身份仍属于“用户管理”详情 Tabs，不拆成独立一级菜单。
- AsyncTask 属于“执行记录”详情，不拆独立页面。
- Console 不提供会话（Conversation Run）管理；`CONV-API-01..04` 不在前端范围内，前端不得引用。

## 修订历史

| 版本 | 日期 | 变更描述 |
|---|---|---|
| V1.11 | 2026-09-11 | 模块分档拆分版 |
| V1.13 | 2026-09-12 | 第三轮 Review 修复：补事实源优先级声明（原型被取代范围）、登记服务新增/编辑路由、声明 Console 不含会话管理 |
| V1.13.1 | 2026-09-12 | 第四轮合理性 Review 裁决修复：取代声明第一部分第 1/4 条改为 D2=A 字段级授权与 `human_policy`；第二部分扩到 16 条（补重新投递/`current_step_name`/测试用户/审计页/trace 搜索/差异确认/能力与模型测试入口/概览两张卡）；新增「显式后置（暂不建设，D7）」 |
| V1.14.1 | 2026-09-13 | 第五轮契约同步（D8~D15/ADR-062..067）9 条对照表 |
| V1.14.2 第六轮 Review 收敛 | 2026-09-13 | 删 `archive/`（V0.8 原型）与「原型被取代范围声明」整节，改为两条事实源规则；删 `06-Knowledge规划/` 占位页与菜单行（ADR-022 规划项，V1 无路由）；服务管理路由删 `/:id` 只读详情（详情即编辑页只读态）；页面头部「交互事实源」统一指向 90 + FE-00 |
