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

## 2. 需求分析

### 2.1 需求概述

| 项目 | 内容 |
|---|---|
| 模块名称 | 模型管理 |
| 需求类型 | 页面/交互模块 |
| 业务背景 | Agent V1 只绑定一个模型，模型配置需提供 OpenAI-compatible Endpoint、Model Name 和 Secret 状态。 |
| 核心目标 | 完成模型列表、新增/编辑、连通性测试；保存后对新请求直接生效。 |
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
| S-07-02 | FEAT-07-02 | E2E | 密钥不回显 | Admin 配置 API Key | 保存后仅 secret_configured；编辑留空=不修改 |
| S-07-03 | FEAT-07-03 | E2E | 连通性测试 | Admin 点击测试 | 显示成功/失败与延迟；超时显示明确错误 |
| E-07-01 | FEAT-07-01 | E2E | Builder 视图 | Builder 打开模型页 | 仅安全选项视图（无 Secret 字段），管理操作不可见 |
| E-07-01 | FEAT-07-01 | integration | services → API → UI | 后端返回字段校验/权限/冲突错误 | 保留当前上下文并显示可定位错误，不出现假成功 |

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

| 字段 | 说明 |
|---|---|
| 名称/标识 | 标识创建后不可改 |
| 协议 | V1 固定 OpenAI 兼容 |
| Base URL | 必填 |
| Model Name | 必填 |
| API Key | 新增必填/更新可输入，详情只显示“已配置” |
| 默认参数 | JSON/typed fields |
| 状态 | 保存直接影响新请求 |



**角色合同**：Builder 仅看安全列表/摘要（id/name/key/enabled/revision；模型可见 protocol/model_name，平台可见 auth_type/configured），不渲染未授权的地址/认证头/模板/Secret 字段。新增、编辑、测试/验证按钮仅 Admin；Builder 仍可从 Agent/Capability 表单选择这些安全依赖。API 双重执行同一权限。

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

## Spec Compliance Matrix

| Spec/Rule | enforcement | 设计影响 | 设计落点 | 验证场景 | 状态/N/A 理由 |
|---|---|---|---|---|---|
| Console-V0.8#READONLY-DETAIL | required | 详情不得变成编辑入口 | §3.3/§3.7 | S-07-01 | applied |
| Console-V0.8#SERVICE-LAYER | required | API 统一从 services 层发起 | §3.5 | S-07-01 | applied |

## 附录：后端追溯

该模块只定义前端状态与交互，不重复定义 DB。DB 字段/索引/约束以对应后端模块 `design-full.md` 为唯一事实源；本文件通过 API ID 追溯到后端 Owner。
