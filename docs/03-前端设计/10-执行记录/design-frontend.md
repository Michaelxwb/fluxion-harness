# 执行记录 前端模块需求与设计简报

> **文档编号**: FE-10-V1.11  
> **文档版本**: V1.11 模块分档拆分版  
> **创建日期**: 2026-09-11  
> **文档状态**: 交互基线已冻结，待仓库 Spec Context 绑定  
> **模板**: `design-frontend.md`  
> **交互事实源**: `../90-Console交互规格.md` + `../fluxion-console-interaction-prototype-v0.8-final.html`

## 1. 文档控制

### 1.1 责任人

| 角色 | 姓名 | 职责范围 |
|---|---|---|
| 前端负责人 | 待定 | 页面、路由、组件、services、E2E |
| 产品/交互 | 待定 | V0.8 交互与字段契约 |
| 后端 Owner | 见 API 映射 | DTO/权限/错误码 Contract |

### 1.2 修订历史

| 版本 | 日期 | 变更描述 |
|---|---|---|
| V1.11 | 2026-09-11 | 从大一统 Console 文档拆成独立产品模块设计 |

## 2. 需求分析

### 2.1 需求概述

| 项目 | 内容 |
|---|---|
| 模块名称 | 执行记录 |
| 需求类型 | 页面/交互模块 |
| 业务背景 | Execution 是同步/异步/混合执行的统一运营视图，AsyncTask 只是 Step 子状态。 |
| 核心目标 | 提供统一执行列表、只读详情 Timeline、AsyncTask 展开、结果/Artifact/投递/错误诊断。 |
| 路由 | `/console/executions, /console/executions/:id` |
| 角色 | Builder / Admin |

### 2.2 功能方案

| 功能ID | 功能名称 | 功能描述 | 优先级 | 来源 |
|---|---|---|---|---|
| FEAT-10-01 | 执行列表 | 状态/投递/时间筛选和详情 | P0 | V0.8 / Playbook / 总设 |
| FEAT-10-02 | 执行详情 | Timeline + AsyncTask + Artifact | P0 | V0.8 / Playbook / 总设 |
| FEAT-10-03 | 取消/重试 | 按后端能力受控暴露 | P1 | V0.8 / Playbook / 总设 |
| FEAT-10-04 | 人工审批 | WAITING_HUMAN 状态展示等待原因/上下文/倒计时，支持继续/终止 | P1 | V0.8 / Playbook / 总设 |

### 2.3 范围与边界

| 类别 | 内容 |
|---|---|
| 范围（In Scope） | 提供统一执行列表、只读详情 Timeline、AsyncTask 展开、结果/Artifact/投递/错误诊断。 |
| 非范围（Out of Scope） | 不设 Async Task 一级页面；列表行操作只有“详情”。 |
| 有意妥协 / 技术债 | 仓库技术栈、组件 API 细节待真实 repo scan 后锁定；产品字段和交互语义已冻结。 |

### 2.4 验收条件

**通用规则**

- Console 仅面向 Builder/Admin，End User 不进入 Console。
- Semi Design 标准列表：左上一个主动作，右上筛选/搜索，中间 Table，右下 PageSize + Pagination。
- 详情默认只读；新增/编辑使用独立 Modal 或独立编辑页，不在详情 Drawer 内直接编辑。
- Secret 不回显；创建后不可变 key 在编辑态只读。
- 所有时间显示 `YYYY-MM-DD HH:mm:ss`。
- 页面组件不得直接裸调用 fetch/axios，统一经 `services/` 或等价数据访问层。

| 场景ID | 功能ID | 测试层级 | 关键真实边界 | 操作步骤 | 预期 UI 结果 |
|---|---|---|---|---|---|
| S-10-01 | FEAT-10-01 | E2E | 多维筛选 | 按状态=WAITING_HUMAN、投递状态=FAILED、trace_id 筛选 | 仅返回匹配执行；字段与 EXE-API-01 DTO 一致 |
| S-10-02 | FEAT-10-04 | E2E | 人工审批闭环 | 对 WAITING_HUMAN 执行点"继续" | 展示 waiting_reason/上下文/倒计时；RESUME 后状态流转；超时后按 HUMAN_TIMEOUT 失败呈现且无操作按钮 |
| S-10-03 | FEAT-10-02 | E2E | Timeline 与 AsyncTask | 展开失败步骤的异步任务 | external task id/轮询状态/结果或错误完整展示 |
| E-10-01 | FEAT-10-03 | E2E | 重试打开新执行 | 点重试后打开响应返回的 new_execution_id | 新详情 parent 指向原执行；原执行仍为 FAILED |
| E-10-01 | FEAT-10-01 | integration | services → API → UI | 后端返回字段校验/权限/冲突错误 | 保留当前上下文并显示可定位错误，不出现假成功 |

## 3. 前端技术设计

### 3.1 技术选型

| 类别 | 选型 | 版本 | 选型理由 |
|---|---|---|---|
| 组件库 | Semi Design | 项目约束 | 与 Console 既定 UI 规范一致 |
| 状态 | Server state + 页面 local state | 待 repo scan | 避免把表单/list query 变成全局事实源 |
| 数据请求 | `services/` 统一封装 | 待 repo scan | 组件中禁止裸 fetch/axios |
| Router | 复用仓库现有 Router | 待 repo scan | 不凭文档替换技术栈 |

### 3.2 页面与路由结构

| 页面 | 路由 | 布局 | 说明 |
|---|---|---|---|
| 执行记录 | `/console/executions, /console/executions/:id` | ConsoleLayout | 提供统一执行列表、只读详情 Timeline、AsyncTask 展开、结果/Artifact/投递/错误诊断。 |

### 3.3 组件设计

| 组件ID | 组件名 | 类型 | 职责 |
|---|---|---|---|
| CMP-10-01 | ExecutionListPage | 容器 | 列表 |
| CMP-10-02 | ExecutionDetailPage | 容器 | 摘要+Timeline |
| CMP-10-03 | ExecutionTimeline | 展示 | Step/AsyncTask/Progress |
| CMP-10-04 | ArtifactLinks | 展示 | 结果制品 |
| CMP-10-05 | HumanApprovalPanel | 容器 | WAITING_HUMAN 人工审批区块（waiting_reason/context 摘要/deadline 倒计时 + 继续/终止） |

### 3.4 组件接口契约与字段

**执行九态**：PENDING/RUNNING/WAITING/WAITING_HUMAN/RETRY_WAIT/CANCELLING/SUCCEEDED/FAILED/CANCELLED。Human 超时是 FAILED/error_code=HUMAN_TIMEOUT，原因单列，不另造执行状态。

**列表 DTO**：完全采用 EXE-API-01 ExecutionSummary：service_name/agent_name/user_name、execution_mode/type、status、delivery_status、trace_id、retry_count、channel_source、current_step、started_at/finished_at。统一 current_step，空值显示“—”。筛选与 Query 同名（含 delivery_status、execution_source）；默认 FORMAL，测试面板另查 TEST。

**详情**：EXE-API-02 ExecutionView + steps/async_tasks/progress_events/artifacts/deliveries/commands。Timeline 采用后端 time/type/status/duration_ms/summary，AsyncTask external_task_id=null 显示“提交结果待确认”，不得伪造 ID。产物按钮经 EXE-API-06 下载字节流，不缓存 302 或对象链接。

**投递状态**：NONE/PENDING/SENDING/RETRY_WAIT/DELIVERED/FAILED/UNKNOWN；业务成功且 UNKNOWN 显示“执行完成，通知送达待确认”，不显示业务重试按钮冒充投递恢复。未知投递停止自动重发，用户可在 IM 用 /result 主动取件。

**人工面板**：WAITING_HUMAN 时展示 waiting_reason/context_summary/human_deadline 倒计时，Admin 且 available_actions 包含相应动作才显示“继续/终止”。只提交 decision/idempotency_key/comment，禁止 input；接受响应后显示“决策已提交”并刷新状态，不假定已经执行。deadline 409 与版本/决策冲突可定位，保留原上下文。Builder 只读。

**取消/重试**：Admin 根据 available_actions 操作；重试成功打开 new_execution_id 并显示 parent_execution_id，重复请求复用幂等键；按执行终态禁止不合法操作。Actor/权限最终以后端为准。

### 3.5 状态与数据流

```text
用户操作
→ 容器组件校验
→ services/10Service
→ 后端 API
→ 统一错误映射
→ query/local state
→ UI 重渲染
```

**API 映射**

| Service 方法/动作 | API ID | 方法/路径或契约 | 后端 Owner |
|---|---|---|---|
| Execution 列表 | `EXE-API-01` | `GET /api/v1/executions` | Service 与 Execution |
| Execution 详情/Timeline | `EXE-API-02` | `GET /api/v1/executions/{execution_id}` | Service 与 Execution |
| 取消 Execution | `EXE-API-03` | `POST /api/v1/executions/{execution_id}/cancel` | Service 与 Execution |
| 重试失败 Execution | `EXE-API-04` | `POST /api/v1/executions/{execution_id}/retry` | Service 与 Execution |
| 下载产物 | `EXE-API-06` | `GET /api/v1/executions/{execution_id}/artifacts/{artifact_id}/download` | Service 与 Execution |

| 人工审批决策 | `EXE-API-05` | `POST /api/v1/executions/{execution_id}/resume`（body: `decision=RESUME / CANCEL`） | Service 与 Execution |

### 3.6 UI 状态

| 视图/交互 | loading | empty | error | success |
|---|---|---|---|---|
| 列表 | Skeleton | 暂无执行 | 错误+重试 | 列表 |
| Timeline | Skeleton | 无步骤异常 | 加载失败 | 时间线 |
| 取消 | 按钮 loading | 不支持时隐藏/禁用 | CANCELLING/远端不可取消说明 | 状态刷新 |

### 3.7 样式与交互规范

- 不重复大页面标题/说明，左侧菜单 + breadcrumb 已表达当前位置。
- 列表页 page size 属于当前列表，不设置全局 page size。
- 行操作固定在最右侧；危险操作二次确认。
- 详情只读，编辑与详情分离。

### 3.8 可访问性与兼容性

- Modal/Drawer 打开后焦点进入容器，关闭后恢复触发点。
- 所有图标按钮具备可访问名称；Form 错误与字段关联。
- 表格主操作可键盘访问。

## 4. 风险与依赖

| 风险ID | 描述 | 影响 | 应对 | 验证场景 |
|---|---|---|---|---|
| RISK-10-01 | 单独建立 Async Task 菜单导致状态割裂 | 高 | AsyncTask 只在 Execution Detail 展开 | E2E/Integration |
| RISK-10-02 | 取消按钮假成功 | 高 | 展示 CANCELLING 和 remote cancel capability | E2E/Integration |

## Spec Compliance Matrix

| Spec/Rule | enforcement | 设计影响 | 设计落点 | 验证场景 | 状态/N/A 理由 |
|---|---|---|---|---|---|
| Console-V0.8#READONLY-DETAIL | required | 详情不得变成编辑入口 | §3.3/§3.7 | S-10-01 | applied |
| Console-V0.8#SERVICE-LAYER | required | API 统一从 services 层发起 | §3.5 | S-10-01 | applied |

## 附录：后端追溯

该模块只定义前端状态与交互，不重复定义 DB。DB 字段/索引/约束以对应后端模块 `design-full.md` 为唯一事实源；本文件通过 API ID 追溯到后端 Owner。
