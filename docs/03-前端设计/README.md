# Console 前端模块设计索引 — V1.11 模块分档拆分版

前端不再使用一篇“大一统 Console 设计”。每个独立产品页面/旅程模块使用一份 `design-frontend.md`，公共 Shell/列表规范单独成模块。

| 序号 | 模块 | 路由 | 分档 | 文档 |
|---|---|---|---|---|
| 00 | Console公共框架 | `/console/*` | Frontend | `00-Console公共框架/design-frontend.md` |
| 01 | 概览 | `/console/overview` | Frontend | `01-概览/design-frontend.md` |
| 02 | 服务管理 | `/console/services, /console/services/new, /console/services/:id, /console/services/:id/edit` | Frontend | `02-服务管理/design-frontend.md` |
| 03 | 智能体管理 | `/console/agents, /console/agents/:id` | Frontend | `03-智能体管理/design-frontend.md` |
| 04 | 能力管理 | `/console/capabilities` | Frontend | `04-能力管理/design-frontend.md` |
| 05 | Skill管理 | `/console/skills, /console/skills/:id` | Frontend | `05-Skill管理/design-frontend.md` |
| 06 | Knowledge规划 | `/console/knowledge` | Frontend | `06-Knowledge规划/design-frontend.md` |
| 07 | 模型管理 | `/console/models` | Frontend | `07-模型管理/design-frontend.md` |
| 08 | 项目平台管理 | `/console/project-platforms` | Frontend | `08-项目平台管理/design-frontend.md` |
| 09 | 用户管理 | `/console/users, /console/users/:id` | Frontend | `09-用户管理/design-frontend.md` |
| 10 | 执行记录 | `/console/executions, /console/executions/:id` | Frontend | `10-执行记录/design-frontend.md` |
| 11 | 审计查询 | `/console/audit-logs` | Frontend | `11-审计查询/design-frontend.md` |

## 事实源优先级

1. 已评审的 `90-Console交互规格.md` 与 `archive/` 下归档的 V0.8 HTML 交互稿（仅作交互形态参考）；
2. 本目录各页面 `design-frontend.md`；
3. 后端模块 API/DB Owner 设计；
4. 旧前端实现只能作为迁移参考，不得覆盖上述交互事实。

**原型被取代范围声明（T-34 / Z-01 / Z-13 / Z-15 / D1 / D2 / D4 / D5 / B11 / B13 / B14 / Z-02 / Z-04 / Z-06 / Z-07 / Z-08 / Z-09 / Z-10 / Z-11 / Z-14 / Y-08）**：`archive/fluxion-console-interaction-prototype-v0.8-final.html`（V1.14 已归档，原路径不再保留）的冻结时间早于 ADR-015 / ADR-021 等后续裁决，**不逐条回写该 HTML**。原型与下文冲突时视为过期载体，以 `90-Console交互规格.md` + 后端模块授权列为准。声明分**正反两向**，避免每轮评审重复发现同一批差异：

**（一）原型有、但已被后续裁决废止的控件与可见性**

1. **角色矩阵与按钮可见性（D2=A 字段级授权，V1.13.1 更新）**：模型管理、项目平台管理的**新增/编辑不再整体收权**——对 Builder 开放；但**敏感字段仅 Admin 可写**：模型 `api_key`、平台 `auth_type`/`auth_schema`，Builder 表单中这些控件**不渲染**（模型侧可见字段见 `90` §6.2，平台侧见 §7.1）。模型连通性测试按钮按模块 19 当前授权列为**仅 Admin**。**智能体 IM 接入（Bot 密钥）写操作仅 Admin**；Builder 对**用户授权 Tab 可见但只读**（原型对 Builder 隐藏该 Tab、并渲染 IM 配置按钮，均不作为事实源）（ADR-021/046 + 模块 09/10/18/19 授权列）。
2. **短页终止（`short_page_terminates`）**：以 ADR-015 为准——默认开启（true）、必须允许显式关闭并提示适用前提。
3. **字段必填性**：以各页面 `design-frontend.md` 的 DTO 映射（及其引用的后端 Create/Update DTO）为准，**不以 HTML 输入框标注为准**（例：能力「输出结果定义」为必填、能力「是否幂等」为三值枚举）。
4. **原型「人工介入（否/必要时/始终）」被 `human_policy` 取代（B11，V1.13.1 更新）**：V1.13 判定的"无 DTO 落点"作废——步骤表单**新增** `human_policy` 控件，取值 `never`（本步不转人工）/ `on_uncertainty`（语义不确定或失败时可转人工）/ `always`（强制人工检查点），见 `90` §2.3 Tab B。原型三值映射：否→`never`、必要时→`on_uncertainty`、始终→`always`。该控件与「失败策略 = `MANUAL`（转人工等待）」**正交且并存**，不互相替代。
5. **知识库菜单**：保留**可点击**的规划页（不置灰），菜单项带「规划中」标记（`90` §1.3 的"置灰"措辞已修正）。
6. **服务「业务范围」表单（D5=A）**：原型的业务范围表单（业务对象类型 / 对象标识参数 / 允许的业务动作 / 范围校验规则）**不作为范围语义事实源**，以 `90` §2.3 Tab C 的 `{type, refs[], attributes?}`（`type` 取自后端 `resource_scope_types` 白名单、`refs[]` 为范围引用、`attributes` 按类型动态渲染）为准；V1 无 Service 级 Scope Schema 与 `Schema Hash`。

**（二）原型缺失、但设计已冻结的内容（实现时以设计为准）**

7. **能力测试**与**模型连通性测试**入口（均为 P0 功能，原型无按钮）：能力为详情页顶部「测试」按钮（`90` §4.7，`CAP-API-05`，Builder + Admin）；模型为列表行内「测试」与详情顶部（`90` §6.3，`MODEL-API-05`，仅 Admin）。
8. **服务测试弹窗的「测试用户」控件**（平台服务能力的用户平台认证必须有该输入）：默认当前登录用户，仅当服务引用的能力实现中存在 `auth_mode=USER_PLATFORM` 时显示（`90` §2.3 Tab D）。
9. **执行详情的审批上下文面板**（`waiting_reason`/`context_summary`/deadline 倒计时）与**产物受权下载**入口。
10. **审计查询页**（原型早于审计设计；Admin 菜单 + 页面见 `90` §15 与 FE-11）。
11. **执行列表的搜索范围包含「追踪标识」（`trace_id`）**；服务列表按「**草稿状态** / **启用状态** / **执行方式**」三项**独立**筛选（原型单一「状态」筛选用作废，V1.13.1 拆分）。
12. **执行详情动作为四个按钮**（「继续 / 终止 / 重新执行 / 重新投递」），按 `available_actions` + 角色渲染，禁用态给出原因（原型的「终止」恒可点、「重试」按投递状态门控均不作为事实源；原型的单个「重试」按钮按 D4=A 拆为「重新执行」与「重新投递」，见 `90` §9.2）。
13. **授权/绑定为「已授权预勾选 + 保存前差异确认（新增 N / 移除 M）+ `PUT` 全量覆盖」**（原型只列未授权对象、逐行增删）。
14. **「当前阶段」渲染 `current_step_name`**（业务可读步骤名；`current_step`（step_key）仅作技术标识，在 hover/次级位置展示）。
15. **步骤表单的 `human_policy` 控件**（三值，见上文第一条声明第 4 点）。
16. **概览的「项目平台数」（`project_platform_count`）与「今日人工超时」（`today_human_timeout`）两张卡**；「待发布 Draft」卡跳转 `/console/services?draft_state=dirty`。

## 显式后置（暂不建设，D7）

以下两项在本轮 Review 中确认**不做**，避免后续轮次重复发现；不在任何页面设计中出现入口、按钮或路由：

1. **END_USER 自助重试**：IM 命令集不含 `/retry`，V1 不新增该命令；失败执行的重试只由 Admin 在 Console 发起（`EXE-API-04`）。
2. **Admin 查看「来源会话消息」入口**：`EXE-API-02` 虽返回 `conversation_id`，Console **不提供**会话消息追溯视图（与 ADR-045「Console 不提供会话管理」一致，不引用 `CONV-API-01..04`）。

两项的共同前提是 Playbook U02/A04 均未要求该能力；若后续确需，需先立产品需求再改本清单。

## 拆分边界

- Agent 的 WeCom/用户授权仍属于“智能体管理”页面，不新增 Channel 一级菜单。
- User 的项目平台认证、Agent 授权、IM 身份仍属于“用户管理”详情 Tabs，不拆成独立一级菜单。
- AsyncTask 属于“执行记录”详情，不拆独立页面。
- Knowledge 当前只有规划页。
- Console 不提供会话（Conversation Run）管理；`CONV-API-01..04` 不在前端范围内，前端不得引用。

## 修订历史

| 版本 | 日期 | 变更描述 |
|---|---|---|
| V1.11 | 2026-09-11 | 模块分档拆分版 |
| V1.13 | 2026-09-12 | 第三轮 Review 修复：补事实源优先级声明（原型被取代范围）、登记服务新增/编辑路由、声明 Console 不含会话管理 |
| V1.13.1 | 2026-09-12 | 第四轮合理性 Review 裁决修复：取代声明第一部分第 1/4 条改为 D2=A 字段级授权与 `human_policy`；第二部分扩到 16 条（补重新投递/`current_step_name`/测试用户/审计页/trace 搜索/差异确认/能力与模型测试入口/概览两张卡）；新增「显式后置（暂不建设，D7）」 |
