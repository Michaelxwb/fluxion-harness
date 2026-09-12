# 模型管理 前端模块需求与设计简报

> **文档编号**: FE-07-V1.11  
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
| V1.13 | 2026-09-12 | 第三轮 Review 修复：超时字段改为 `request_timeout_seconds`（秒，默认 60，范围 1..600）；补 `extra_headers`（仅 Admin 展示、仅非敏感 Header）与 `api_key_configured` 统一命名；§3.4 加角色可见性列（Builder 不可见 `base_url`/`default_parameters`/`extra_headers`）；补场景 `S-07-04`；悬空 FEAT 引用（连通性测试场景）改挂 `FEAT-07-02`；场景 ID 前缀拆分（integration → `I-07-01`） |
| V1.13.1 | 2026-09-12 | 第四轮 Review 修复（D2=A 字段级授权，与 `90` §6 一致）：`MODEL-API-02/04` 由「仅 Admin」改为 **Builder + Admin（敏感字段仅 Admin）**——新增/编辑按钮对 Builder 渲染，`api_key`/`default_parameters`/`extra_headers` 控件 Builder 不渲染且提交体不含；非 Admin 携带 `api_key` → 403 `FIELD_ADMIN_ONLY`且原子拒绝；`MODEL-API-05` 维持仅 Admin（已确认）；重写 §3.4 角色可见性/角色合同，改 `E-07-01` 断言并新增 `S-07-05`；新增 `### 3.4.1 连通性测试（Z-04，`MODEL-API-05`）` 块（两入口共用 `ModelTestDialog`、仅 Admin、只做 ping 不回输出、`latency_ms` 渲染 `N ms`、`timeout_seconds` 不得超 hard limit、不落执行/不产运营统计）；新增场景 `E-07-02`；§4 补 `RISK-07-03`（模块 19 需同步 Builder 投影，否则 Builder 编辑拿不到 `base_url`） |

## 2. 需求分析

### 2.1 需求概述

| 项目 | 内容 |
|---|---|
| 模块名称 | 模型管理 |
| 需求类型 | 页面/交互模块 |
| 业务背景 | Agent V1 只绑定一个模型，模型配置需提供 OpenAI-compatible Endpoint、Model Name 和 Secret 状态。 |
| 核心目标 | 完成模型列表、新增/编辑、连通性测试；保存后对新请求直接生效。写权限按 **D2=A 字段级授权**：新增/编辑对 **Builder 开放**（按钮渲染），**敏感字段仅 Admin 可写**（`api_key`；`default_parameters`/`extra_headers` 为模块 19 Builder 隐藏集）；**连通性测试仍仅 Admin**。 |
| 路由 | `/console/models` |
| 角色 | Builder / Admin |

### 2.2 功能方案

| 功能ID | 功能名称 | 功能描述 | 优先级 | 来源 |
|---|---|---|---|---|
| FEAT-07-01 | 模型 CRUD | OpenAI-compatible 配置 | P0 | V0.8 / Playbook / 总设 |
| FEAT-07-02 | 连通测试 | 测试 Endpoint/Secret/Model | P0 | V0.8 / Playbook / 总设 |

### 2.3 范围与边界

| 类别 | 内容 |
|---|---|
| 范围（In Scope） | 完成模型列表、新增/编辑、连通性测试；保存后对新请求直接生效。 |
| 非范围（Out of Scope） | 不支持 Internal Gateway 协议，不建设 Model Draft/Publish。 |
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
| S-07-01 | FEAT-07-01 | E2E | 标识不可改 | 编辑已建模型 | 标识字段只读；名称/参数可改即生效 |
| S-07-02 | FEAT-07-02 | E2E | 密钥不回显 | Admin 配置 API Key | 保存后列表与详情只显示 `api_key_configured=true`；编辑留空 = 不修改；页面任何位置不出现密钥明文 |
| S-07-03 | FEAT-07-02 | E2E | 连通性测试 | Admin 点击测试 | 显示成功/失败与延迟；超时显示明确错误 |
| S-07-04 | FEAT-07-01 | E2E | 超时单位与敏感 Header | Admin 在编辑表单「请求超时时间（秒）」填 `120` 并保存 | 读回详情与 DB 均为 `request_timeout_seconds=120`（秒，非毫秒）；列表**不出现** `extra_headers` 值；Builder 打开同一模型的详情可**看到** `base_url`/`default_parameters`（其可写字段），但**看不到** `extra_headers` |
| S-07-05 | FEAT-07-01 | E2E | Builder 字段级写权限（D2=A） | Builder 新增一个模型（表单不出现 API Key 控件） | 请求体**不含** `api_key`；创建成功（200/201）且 `api_key_configured=false`；Builder 可再编辑非敏感字段（`name`/`base_url`/`model_name`/`request_timeout_seconds`/`enabled`）并保存成功 |
| E-07-01 | FEAT-07-01 | E2E | Builder 视图与字段级写权限 | Builder 打开模型列表与详情；并直接调用 `MODEL-API-02` 携带 `api_key`；另看连通性测试入口 | 列表/详情只渲染 `name`/`key`/`protocol`/`model_name`/`enabled`/`revision`/`configured`/`api_key_configured`；**不渲染** `extra_headers`（`base_url`/`default_parameters` 在详情与表单可见，因为 Builder 需要编辑它们）；新增/编辑按钮**对 Builder 渲染**（可编辑非敏感字段，表单不渲染 `api_key`/`default_parameters`/`extra_headers`）；测试按钮**仅 Admin**（Builder 不渲染，直调 `MODEL-API-05` 得 403）；Builder 直调 `MODEL-API-02` 携带 `api_key` 得 **403 `FIELD_ADMIN_ONLY`** 且**原子拒绝**（同一请求的非敏感字段变更也不生效） |
| E-07-02 | FEAT-07-02 | E2E | 连通性测试 | Admin 对一个会超时的模型点「测试」；另把 `timeout_seconds` 填为大于该模型 `request_timeout_seconds` 的值再提交 | 弹窗显示**明确的超时错误**（非“未知错误”）且已填超时值**不被清空**；**不产生执行记录**（执行列表无新增）；`timeout_seconds` 超过该模型 hard limit 时**就地报错且不发请求**（Network 面板无 `MODEL-API-05`） |
| I-07-01 | FEAT-07-01 | integration | services → API → UI | 后端返回字段校验/权限/冲突错误 | 保留当前上下文并显示可定位错误，不出现假成功 |

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
| 模型管理 | `/console/models` | ConsoleLayout | 完成模型列表、新增/编辑、连通性测试；保存后对新请求直接生效。 |

### 3.3 组件设计

| 组件ID | 组件名 | 类型 | 职责 |
|---|---|---|---|
| CMP-07-01 | ModelListPage | 容器 | 标准列表 |
| CMP-07-02 | ModelFormModal | 容器 | 新增/编辑 |
| CMP-07-03 | ModelTestDialog | 容器 | 连通测试 |

### 3.4 组件接口契约与字段

| 字段 | DTO 字段名 | 必填 | Admin 可见 | Builder 可见 | 说明 |
|---|---|---:|---:|---:|---|
| 名称 | `name` | 是 | 是 | 是 | |
| 标识 | `key` | 是 | 是 | 是 | 创建后不可改 |
| 协议 | `protocol` | 是 | 是 | 是 | V1 固定 OpenAI 兼容 |
| 接口地址 | `base_url` | 是 | 是（仅编辑/详情） | 详情**否**；新增/编辑表单**是** | 列表对两角色都不渲染；新建时 Builder 必须能填（`MODEL-API-02` 必填，模块 19 投影中 Builder 详情不含）；非敏感字段，Builder 亦可写（受 SSRF allowlist 约束） |
| Model Name | `model_name` | 是 | 是 | 是 | |
| API Key | `api_key` | 新增必填 / 更新留空 | 仅输入框（**仅 Admin 渲染**） | **否** | 永不回显；状态字段名统一为 `api_key_configured`；Builder 提交体不含该字段，非 Admin 携带（含 `null`/空串）→ 403 `FIELD_ADMIN_ONLY`且**原子拒绝** |
| 请求超时时间（秒） | `request_timeout_seconds` | 是 | 是 | 详情**否**；新增/编辑表单**是** | 单位**秒**，默认 `60`，范围 1..600；Builder 表单可填（`90` §6.2） |
| 默认参数 | `default_parameters` | 否 | 是（仅编辑/详情） | **是**（Builder 与 Admin 均可渲染） | 温度/最大输出令牌数等，非凭据；列表不渲染 |
| 额外请求头 | `extra_headers` | 否 | 是（仅编辑/详情） | **否**（**仅 Admin 渲染**） | JSON 文本域，示例 `{"X-Tenant":"prod"}`；**仅非敏感 Header，不得放凭据**；列表不展示；模块 19 Builder 投影隐藏集 |
| 启用状态 | `enabled` | 是 | 是 | 是 | 保存直接影响新请求 |
| 版本 | `revision` | — | 是 | 是 | 编辑乐观锁 |
| 配置态 | `configured` / `api_key_configured` | — | 是 | 是 | 后端返回，前端不推导 |

**角色可见性（V1.13.1 按 D2=A 更新，与 `90` §6 一致）**：**列表**对两角色返回同一组字段（`key`/`name`/`protocol`/`model_name`/`enabled`/`revision`/`api_key_configured`，不含 `base_url`/`default_parameters`/`extra_headers`，模块 19 已冻结）。**新增/编辑表单**：Builder 可填 `name`/`key`/`base_url`/`model_name`/`protocol`/`default_parameters`/`request_timeout_seconds`/`enabled`；**`api_key`/`extra_headers` 仅 Admin 渲染**，Builder 表单不渲染这两个控件且提交体不含它们。**详情**：Builder 可见其可写字段（`base_url`/`default_parameters`/`request_timeout_seconds`）——即「能写的就能看见」，避免打开编辑表单拿不到当前值；`api_key`/`extra_headers` 两角色详情均不回显。**新增/编辑按钮对 Builder 渲染**；**连通性测试按钮仅 Admin 渲染**。



**角色合同（D2=A 字段级授权）**：Builder 可见模型安全列表/摘要（`id`/`name`/`key`/`protocol`/`model_name`/`enabled`/`revision`/`configured`/`api_key_configured`），**可新增/编辑非敏感字段**（`MODEL-API-02/04` 对 Builder 开放），表单不渲染 `api_key`/`extra_headers` 控件（`default_parameters` 可渲染，非凭据）；**连通性测试仍仅 Admin**（`MODEL-API-05`：测试会用已存 Secret 向 `base_url` 发出站请求，属凭据使用 + 出站探测，Builder 不渲染按钮、直调得 403）。非 Admin 请求体出现 `api_key`（含 `null`/空串）→ **403 `FIELD_ADMIN_ONLY`且原子拒绝**（不是接口级“无权限访问”，同请求的非敏感字段变更也不生效）；前端按字段级错误定位到表单并保留输入，不由前端“先过滤”掩盖。Builder 仍可从 Agent/Capability 表单选择这些安全依赖（`MODEL-API-01` 安全只读 DTO）。API 双重执行同一权限。

### 3.4.1 连通性测试（Z-04，`MODEL-API-05`）

契约与文案见 `../90-Console交互规格.md` §6.3（`FEAT-07-02`）；组件为 `CMP-07-03 ModelTestDialog`（两处入口共用同一个弹窗）。

| 项 | 前端规范 |
|---|---|
| 入口 | ① 列表**行内**「测试」；② 模型**详情页顶部**「测试」。两处打开**同一个** `ModelTestDialog` |
| 权限 | **仅 Admin**（与模块 19 `MODEL-API-05` 授权列一致）：Builder **两处按钮均不渲染**，直调得 **403** |
| 输入 | 仅两个可选参数：`prompt`（可空；留空即用后端默认短探针，**不落库、不回显**）与 `timeout_seconds`（默认取该模型 `request_timeout_seconds`，范围 1..600，**不得超过**该 hard limit；超出**就地报错且不发请求**） |
| 输出 | **只做 ping**：`ok` + `latency_ms`（渲染为 `N ms`）+ `model_name`（实际模型）+ 可选 `provider_request_id`（可复制）；**不返回模型输出内容、不显示 prompt 响应文本** |
| 失败呈现 | 弹窗内错误条显示脱敏 `error_code`/`error_message`；**超时给明确错误**（非“未知错误”）；**不因失败清空已填的超时值** |
| 副作用 | 测试结果不写入执行记录、**不产生 Execution、不影响运营统计**；仅落审计 |
| 并发 | 同步请求；按钮 loading 期间不可重复点击 |

### 3.5 状态与数据流

```text
用户操作
→ 容器组件校验
→ services/07Service
→ 后端 API
→ 统一错误映射
→ query/local state
→ UI 重渲染
```

**API 映射**

| Service 方法/动作 | API ID | 方法/路径或契约 | 后端 Owner |
|---|---|---|---|
| 模型列表 | `MODEL-API-01` | `GET /api/v1/models` | 模型配置与调用 |
| 新增模型 | `MODEL-API-02` | `POST /api/v1/models` | 模型配置与调用 |
| 模型详情 | `MODEL-API-03` | `GET /api/v1/models/{model_id}` | 模型配置与调用 |
| 编辑模型 | `MODEL-API-04` | `PUT /api/v1/models/{model_id}` | 模型配置与调用 |
| 模型连通性测试 | `MODEL-API-05` | `POST /api/v1/models/{model_id}/test` | 模型配置与调用 |

### 3.6 UI 状态

| 视图/交互 | loading | empty | error | success |
|---|---|---|---|---|
| 列表 | Skeleton | 暂无模型 | 错误+重试 | 列表 |
| 测试 | 按钮 loading | — | 测试失败原因 | 成功摘要 |

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
| RISK-07-01 | API Key 明文回显 | 高 | 后端只返回 configured，前端永不渲染 secret | E2E/Integration |
| RISK-07-02 | 引入模型发布版本增加复杂度 | 中 | V1 direct-effect + audit | E2E/Integration |
| RISK-07-03 | D2=A 字段级授权依赖后端模块 `19` 同步 Builder 投影 | 中 | **V1.13.1 已闭合**：模块 `19` 的 Builder 投影已改为「能写的就能看见」——`MODEL-API-03` 对 Builder 返回 `base_url`/`default_parameters`/`request_timeout_seconds`，仅 `api_key`/`extra_headers` 不回显；`MODEL-API-05` 维持仅 Admin（若后续放开，§3.4.1 与 `E-07-01` 需同步改口） | E2E/Integration |

## Spec Compliance Matrix

| Spec/Rule | enforcement | 设计影响 | 设计落点 | 验证场景 | 状态/N/A 理由 |
|---|---|---|---|---|---|
| Console-V0.8#READONLY-DETAIL | required | 详情不得变成编辑入口 | §3.3/§3.7 | S-07-01 | applied |
| Console-V0.8#SERVICE-LAYER | required | API 统一从 services 层发起 | §3.5 | S-07-01 | applied |

## 附录：后端追溯

该模块只定义前端状态与交互，不重复定义 DB。DB 字段/索引/约束以对应后端模块 `design-full.md` 为唯一事实源；本文件通过 API ID 追溯到后端 Owner。
