# 服务管理 前端模块需求与设计简报

> **文档编号**: FE-02-V1.11  
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
| 模块名称 | 服务管理 |
| 需求类型 | 页面/交互模块 |
| 业务背景 | Service 是唯一正式发布业务对象，需要 Draft→Validate/Test→Publish 的明确旅程。 |
| 核心目标 | 完成服务列表、新增、Draft 编辑、步骤编排、业务范围、测试与发布，以及只读详情。 |
| 路由 | `/console/services, /console/services/:id` |
| 角色 | Builder / Admin（正式发布 Admin） |

### 2.2 功能方案

| 功能ID | 功能名称 | 功能描述 | 优先级 | 来源 |
|---|---|---|---|---|
| FEAT-02-01 | 服务列表 | 搜索/筛选/分页/详情/编辑 | P0 | V0.8 / Playbook / 总设 |
| FEAT-02-02 | 新增服务 | 主 Agent 必选 | P0 | V0.8 / Playbook / 总设 |
| FEAT-02-03 | Draft 编辑 | 基本信息/步骤/范围 | P0 | V0.8 / Playbook / 总设 |
| FEAT-02-04 | 校验测试发布 | Builder 校验测试，Admin 发布 | P0 | V0.8 / Playbook / 总设 |

### 2.3 范围与边界

| 类别 | 内容 |
|---|---|
| 范围（In Scope） | 完成服务列表、新增、Draft 编辑、步骤编排、业务范围、测试与发布，以及只读详情。 |
| 非范围（Out of Scope） | 详情模式不允许新增/编辑/删除步骤；发布动作仅 Admin（按钮对 Builder 不渲染）。已发布 Service 的管理员手工发起执行（后端支持，Console 后置）；Release 回滚/切回历史版本（后端支持，Console 后置）。 |
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
| S-02-01 | FEAT-02-01 | E2E | 主智能体必选 | 新增服务不选主智能体提交 | 表单校验失败并定位字段；选后创建成功 |
| S-02-02 | FEAT-02-03 | E2E | Draft 保存乐观锁 | 两个 Builder 同时编辑同一 Draft | 后保存者 409，提示刷新重试 |
| S-02-03 | FEAT-02-05 | E2E | 测试-发布两阶段 | Builder 运行 Draft 测试（DRY_RUN）→ Admin 发布 | 测试结果绑定 draft_revision；发布仅 Admin 可见且成功后 Release 列表 +1 |
| S-02-04 | FEAT-02-06 | E2E | 紧急停用 | Admin 列表行紧急停用已发布服务 | 二次确认后 enabled=false 即时生效；Builder 不可见该操作 |
| E-02-01 | FEAT-02-05 | E2E | 发布按钮角色 | Builder 打开 Tab D | 无发布按钮（不渲染）；Admin 有发布+Release 预览 |
| E-02-01 | FEAT-02-01 | integration | services → API → UI | 后端返回字段校验/权限/冲突错误 | 保留当前上下文并显示可定位错误，不出现假成功 |

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
| 服务管理 | `/console/services, /console/services/:id` | ConsoleLayout | 完成服务列表、新增、Draft 编辑、步骤编排、业务范围、测试与发布，以及只读详情。 |

### 3.3 组件设计

| 组件ID | 组件名 | 类型 | 职责 |
|---|---|---|---|
| CMP-02-01 | ServiceListPage | 容器 | 标准列表 |
| CMP-02-02 | ServiceCreateModal | 容器 | 创建基础字段 |
| CMP-02-03 | ServiceDraftEditor | 容器 | 四 Tab 编辑 |
| CMP-02-04 | ServiceStepEditor | 容器 | 步骤新增/编辑 |
| CMP-02-05 | ServiceReleasePanel | 容器 | 校验/测试/发布 |

### 3.4 组件接口契约与字段

**列表字段**

| 字段 | 说明 |
|---|---|
| 服务名称/标识 | 可搜索 |
| 主智能体 | 创建必选 |
| 执行方式 | 筛选；AGENTIC（智能体自主）/DETERMINISTIC（固定流程）/HYBRID（混合），枚举已冻结 |
| 已发布版本 | 无则 `—` |
| 草稿状态/启用状态 | 筛选 |
| 更新时间 | 完整时间 |
| 操作 | 详情、编辑 |

**新增字段**

| 字段 | 必填 | 创建后编辑 |
|---|---:|---:|
| 服务名称 | 是 | 是 |
| 服务标识 | 是 | 否 |
| 服务说明 | 否 | 是 |
| 主智能体 | 是 | Draft 可改 |
| 执行方式 | 是 | 是 |
| 启用状态 | 是 | 创建后仅 Admin 即时启停，Draft 只读 |

**执行步骤字段**：步骤名称、Step 类型、执行对象、输入映射、输出变量、超时、失败策略、最大重试、人工说明、步骤说明。



**保存合同**：前端直接构造模块 05 service-draft-schema：主 Agent→primary_agent_id；步骤名称→name、说明→description、稳定键→step_key、类型→type；执行对象→agent_id/capability_key；范围→resource_scope.scope_type/input_schema。输入映射控件输出 `{source:INPUT,path:JSON Pointer}`、`{source:STEP,step_key,path}` 或 `{literal:value}`，不使用字符串插值。output_var 仅显示别名。

失败策略四项：终止 FAIL_FAST / 有界重试 RETRY / 等待人工 MANUAL / 显式跳过 SKIP_ON_ERROR。WAIT 必填 wait_seconds，HUMAN 必填 human_prompt，人工默认 24h；同一表单提交的 schema 与后端共用文档中的 JSON Schema 样例测试。非法引用定位到具体 Step/字段。

**测试面板**：传当前 draft_revision/test_user_id/input/mode/idempotency_key；默认 DRY_RUN，无 mock 返回明确错误。结果显示 TEST、修订、test_mode、expired；草稿修改后旧测试显示“草稿已变更，请重测”。测试列表传 execution_source=TEST，不能混入运营统计。

**即时启停**：Admin 在列表行/详情独立“紧急停用/恢复”，调用 SVC-API-10，停用二次确认并刷新 enabled。Draft 编辑只读展示即时 enabled，不将其放进 draft_payload；保存/发布不能覆盖该开关，Builder 不显示操作。

### 3.5 状态与数据流

```text
用户操作
→ 容器组件校验
→ services/02Service
→ 后端 API
→ 统一错误映射
→ query/local state
→ UI 重渲染
```

**API 映射**

| Service 方法/动作 | API ID | 方法/路径或契约 | 后端 Owner |
|---|---|---|---|
| Service 列表 | `SVC-API-01` | `GET /api/v1/services` | Service 与 Execution |
| 创建 Service | `SVC-API-02` | `POST /api/v1/services` | Service 与 Execution |
| Service 详情 | `SVC-API-03` | `GET /api/v1/services/{service_id}` | Service 与 Execution |
| 保存 Service Draft | `SVC-API-04` | `PUT /api/v1/services/{service_id}/draft` | Service 与 Execution |
| 校验 Service Draft | `SVC-API-05` | `POST /api/v1/services/{service_id}/validate` | Service 与 Execution |
| 测试 Service Draft | `SVC-API-06` | `POST /api/v1/services/{service_id}/test` | Service 与 Execution |
| 发布 Service | `SVC-API-07` | `POST /api/v1/services/{service_id}/publish` | Service 与 Execution |
| 紧急停用/恢复 | `SVC-API-10` | `PATCH /api/v1/services/{service_id}/enabled` | Service 与 Execution |

| Release 列表 | `SVC-API-08` | `GET /api/v1/services/{service_id}/releases` | Service 与 Execution |
| Release 详情 | `SVC-API-09` | `GET /api/v1/services/{service_id}/releases/{release_id}` | Service 与 Execution |

### 3.6 UI 状态

| 视图/交互 | loading | empty | error | success |
|---|---|---|---|---|
| 列表 | Skeleton | 暂无服务 | 错误+重试 | 表格+分页 |
| Draft | Skeleton | 无步骤 | 保存/校验错误定位字段 | 编辑器 |
| 发布 | 按钮 loading | 无 Release | 校验失败/409 revision | 发布成功+Release |

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
| RISK-02-01 | 发布时 Draft 被并发修改 | 高 | revision/409 冲突后要求刷新 | E2E/Integration |
| RISK-02-02 | 把 Service 编辑成在线 Runtime truth | 高 | 保存只改 Draft；发布产生 immutable Release | E2E/Integration |

## Spec Compliance Matrix

| Spec/Rule | enforcement | 设计影响 | 设计落点 | 验证场景 | 状态/N/A 理由 |
|---|---|---|---|---|---|
| Console-V0.8#READONLY-DETAIL | required | 详情不得变成编辑入口 | §3.3/§3.7 | S-02-01 | applied |
| Console-V0.8#SERVICE-LAYER | required | API 统一从 services 层发起 | §3.5 | S-02-01 | applied |

## 附录：后端追溯

该模块只定义前端状态与交互，不重复定义 DB。DB 字段/索引/约束以对应后端模块 `design-full.md` 为唯一事实源；本文件通过 API ID 追溯到后端 Owner。
