# Knowledge规划 前端模块需求与设计简报

> **文档编号**: FE-06-V1.11  
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
| 模块名称 | Knowledge规划 |
| 需求类型 | 页面/交互模块 |
| 业务背景 | Knowledge 在当前阶段只保留架构扩展点，尚未冻结生产 Provider/API/DB。 |
| 核心目标 | 明确显示“规划中”，防止前端先造假 CRUD 反向锁死后端模型。 |
| 路由 | `/console/knowledge` |
| 角色 | Builder / Admin |

### 2.2 功能方案

| 功能ID | 功能名称 | 功能描述 | 优先级 | 来源 |
|---|---|---|---|---|
| FEAT-06-01 | 规划状态 | 显示范围与未决项 | P2 | V0.8 / Playbook / 总设 |

### 2.3 范围与边界

| 类别 | 内容 |
|---|---|
| 范围（In Scope） | 明确显示“规划中”，防止前端先造假 CRUD 反向锁死后端模型。 |
| 非范围（Out of Scope） | 所有 Knowledge CRUD、Provider 选择、权限、索引/向量库字段均不冻结。 |
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
| S-06-01 | FEAT-06-01 | E2E | Browser → Router → services → API → UI | 打开页面并完成主操作 | 页面字段、按钮、状态与 API Contract 一致 |
| E-06-01 | FEAT-06-01 | integration | services → API → UI | 后端返回字段校验/权限/冲突错误 | 保留当前上下文并显示可定位错误，不出现假成功 |

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
| Knowledge规划 | `/console/knowledge` | ConsoleLayout | 明确显示“规划中”，防止前端先造假 CRUD 反向锁死后端模型。 |

### 3.3 组件设计

| 组件ID | 组件名 | 类型 | 职责 |
|---|---|---|---|
| CMP-06-01 | KnowledgePlannedPage | 展示 | 规划说明 |

### 3.4 组件接口契约与字段

页面只展示：`规划中`、目标边界、当前未冻结的 Provider/API/DB 说明；无新增按钮、无假列表。



### 3.5 状态与数据流

```text
用户操作
→ 容器组件校验
→ services/06Service
→ 后端 API
→ 统一错误映射
→ query/local state
→ UI 重渲染
```

**API 映射**

| Service 方法/动作 | API ID | 方法/路径或契约 | 后端 Owner |
|---|---|---|---|

### 3.6 UI 状态

| 视图/交互 | loading | empty | error | success |
|---|---|---|---|---|
| 规划页 | — | — | — | 规划说明 |

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
| RISK-06-01 | 为了页面完整度提前造 Knowledge CRUD | 高 | 无 API 时禁止出现新增/编辑/列表假数据 | E2E/Integration |

## Spec Compliance Matrix

| Spec/Rule | enforcement | 设计影响 | 设计落点 | 验证场景 | 状态/N/A 理由 |
|---|---|---|---|---|---|
| Console-V0.8#READONLY-DETAIL | required | 详情不得变成编辑入口 | §3.3/§3.7 | S-06-01 | applied |
| Console-V0.8#SERVICE-LAYER | required | API 统一从 services 层发起 | §3.5 | S-06-01 | applied |

## 附录：后端追溯

该模块只定义前端状态与交互，不重复定义 DB。DB 字段/索引/约束以对应后端模块 `design-full.md` 为唯一事实源；本文件通过 API ID 追溯到后端 Owner。
