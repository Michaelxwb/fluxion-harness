# 智能体管理 前端模块需求与设计简报

> **文档编号**: FE-03-V1.11  
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
| 模块名称 | 智能体管理 |
| 需求类型 | 页面/交互模块 |
| 业务背景 | Agent Definition 是配置对象，直接生效但资源绑定、IM 接入和用户授权应在独立 Tab 管理。 |
| 核心目标 | 管理 Agent 基础配置、直接能力、Skill、可调用服务、IM 接入和用户授权，并明确每种关系方向。 |
| 路由 | `/console/agents, /console/agents/:id` |
| 角色 | Builder / Admin |

### 2.2 功能方案

| 功能ID | 功能名称 | 功能描述 | 优先级 | 来源 |
|---|---|---|---|---|
| FEAT-03-01 | 智能体列表 | 模型/资源计数/IM/授权用户摘要 | P0 | V0.8 / Playbook / 总设 |
| FEAT-03-02 | 基础配置 | 单模型/提示词/Memory | P0 | V0.8 / Playbook / 总设 |
| FEAT-03-03 | 资源绑定 | 能力/Skill/可调用服务 | P0 | V0.8 / Playbook / 总设 |
| FEAT-03-04 | IM 接入 | WeCom Bot 1:1 Agent | P0 | V0.8 / Playbook / 总设 |
| FEAT-03-05 | 用户授权 | Admin 管理 AgentAccessGrant | P0 | V0.8 / Playbook / 总设 |

### 2.3 范围与边界

| 类别 | 内容 |
|---|---|
| 范围（In Scope） | 管理 Agent 基础配置、直接能力、Skill、可调用服务、IM 接入和用户授权，并明确每种关系方向。 |
| 非范围（Out of Scope） | 不在新增 Agent 表单中配置能力、Skill、知识、可调用服务、IM、用户授权。 |
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
| S-03-01 | FEAT-03-01 | E2E | 必选模型选项 | Builder 新增智能体选择模型 | 模型下拉来自 MODEL-API-01 安全选项（无 Secret）；保存成功 |
| S-03-02 | FEAT-03-03 | E2E | 绑定选择弹窗 | Builder 在能力/Skill Tab 点绑定 | 选择弹窗展示可绑定对象；保存后 Tab 列表即时刷新 |
| S-03-03 | FEAT-03-05 | E2E | IM 接入配置 | Admin 配置企微 Bot 密钥 | 保存后仅显示 secret_configured=true；连接状态只读展示 |
| E-03-01 | FEAT-03-06 | E2E | 用户授权 Tab 角色 | Builder 查看智能体详情 | 用户授权 Tab 隐藏或只读（Admin 可管理） |
| E-03-01 | FEAT-03-01 | integration | services → API → UI | 后端返回字段校验/权限/冲突错误 | 保留当前上下文并显示可定位错误，不出现假成功 |

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
| 智能体管理 | `/console/agents, /console/agents/:id` | ConsoleLayout | 管理 Agent 基础配置、直接能力、Skill、可调用服务、IM 接入和用户授权，并明确每种关系方向。 |

### 3.3 组件设计

| 组件ID | 组件名 | 类型 | 职责 |
|---|---|---|---|
| CMP-03-01 | AgentListPage | 容器 | 标准列表 |
| CMP-03-02 | AgentBaseForm | 容器 | 基础新增/编辑 |
| CMP-03-03 | AgentDetailTabs | 容器 | 资源/IM/授权 Tabs |
| CMP-03-04 | AgentWeComPanel | 容器 | Bot 配置+连接状态 |
| CMP-03-05 | AgentGrantPanel | 容器 | 授权用户 |

### 3.4 组件接口契约与字段

**基础字段**：名称、标识（创建后不可改）、说明、模型配置（V1 一个）、系统提示词、记忆策略、状态。

**直接能力 Tab**：能力名称/标识/实现类型/风险/状态/来源=直接绑定；支持绑定/解绑。

**Skill Tab**：仅选择已启用且校验通过 Skill；展示 current artifact checksum/SDK/归属标签。

**可调用服务 Tab**：表示 Agent 可调用的子服务，不等价于 Service.primary_agent。

**IM Tab**：企业微信、WebSocket、Bot ID、Secret 输入、启用状态、连接状态/最近连接时间。

**用户授权 Tab**：Admin 添加/撤销用户；Builder 只读或隐藏。



**依赖选择**：模型下拉经 MODEL-API-01 的 Builder 安全 DTO（id/name/model_name/enabled）查询，保存 model_config_id；无可用模型明确空态，不要求 Builder 手填 UUID 或获取 Secret。MemoryPolicy 表单字段 allowed_keys/max_value_bytes/max_items，分别映射 Agent.memory_policy；敏感授权/IM Bot 写操作仅 Admin。

### 3.5 状态与数据流

```text
用户操作
→ 容器组件校验
→ services/03Service
→ 后端 API
→ 统一错误映射
→ query/local state
→ UI 重渲染
```

**API 映射**

| Service 方法/动作 | API ID | 方法/路径或契约 | 后端 Owner |
|---|---|---|---|
| Agent 列表 | `AGENT-API-01` | `GET /api/v1/agents` | Agent Core 与 Agent Executor |
| 创建 Agent | `AGENT-API-02` | `POST /api/v1/agents` | Agent Core 与 Agent Executor |
| Agent 详情 | `AGENT-API-03` | `GET /api/v1/agents/{agent_id}` | Agent Core 与 Agent Executor |
| 编辑 Agent 基本配置 | `AGENT-API-04` | `PUT /api/v1/agents/{agent_id}` | Agent Core 与 Agent Executor |
| 覆盖 Agent 直接能力 | `AGENT-API-05` | `PUT /api/v1/agents/{agent_id}/capabilities` | Agent Core 与 Agent Executor |
| 覆盖 Agent Skill | `AGENT-API-06` | `PUT /api/v1/agents/{agent_id}/skills` | Agent Core 与 Agent Executor |
| 覆盖可调用子服务 | `AGENT-API-07` | `PUT /api/v1/agents/{agent_id}/services` | Agent Core 与 Agent Executor |
| 有效能力分析 | `AGENT-API-08` | `GET /api/v1/agents/{agent_id}/effective-capabilities` | Agent Core 与 Agent Executor |
| 读取 Agent WeCom 接入 | `CH-API-01` | `GET /api/v1/agents/{agent_id}/channel/wecom` | Channel Gateway |
| 保存 Agent WeCom 接入 | `CH-API-02` | `PUT /api/v1/agents/{agent_id}/channel/wecom` | Channel Gateway |
| Agent 反向授权用户 | `USR-API-07` | `GET /api/v1/agents/{agent_id}/users` | 用户与 Agent 授权 |
| 覆盖 Agent 授权用户 | `USR-API-08` | `PUT /api/v1/agents/{agent_id}/users` | 用户与 Agent 授权 |

### 3.6 UI 状态

| 视图/交互 | loading | empty | error | success |
|---|---|---|---|---|
| 列表 | Skeleton | 暂无 Agent | 错误+重试 | 列表 |
| IM连接状态 | 局部 loading | 未配置 | 连接异常诊断 | 已连接/断开状态 |
| 用户授权 | loading | 暂无授权 | 权限失败 | 授权表 |

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
| RISK-03-01 | 把 Skill 依赖自动变成 Agent 直接 Tool | 高 | 只显示 effective capability 分析，不自动创建 binding | E2E/Integration |
| RISK-03-02 | Bot ID 复用到多个 Agent | 高 | 前端校验 + 后端唯一约束 | E2E/Integration |

## Spec Compliance Matrix

| Spec/Rule | enforcement | 设计影响 | 设计落点 | 验证场景 | 状态/N/A 理由 |
|---|---|---|---|---|---|
| Console-V0.8#READONLY-DETAIL | required | 详情不得变成编辑入口 | §3.3/§3.7 | S-03-01 | applied |
| Console-V0.8#SERVICE-LAYER | required | API 统一从 services 层发起 | §3.5 | S-03-01 | applied |

## 附录：后端追溯

该模块只定义前端状态与交互，不重复定义 DB。DB 字段/索引/约束以对应后端模块 `design-full.md` 为唯一事实源；本文件通过 API ID 追溯到后端 Owner。
