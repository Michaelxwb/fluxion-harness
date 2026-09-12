# 能力管理 前端模块需求与设计简报

> **文档编号**: FE-04-V1.11  
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
| 模块名称 | 能力管理 |
| 需求类型 | 页面/交互模块 |
| 业务背景 | Capability 是稳定原子能力边界，需要屏蔽 Platform Service/HTTP/MCP/Sandbox 的具体实现差异，并承担自动分页。 |
| 核心目标 | 完成 Capability Contract、四类实现配置、Data Retrieval Policy、测试和只读详情。 |
| 路由 | `/console/capabilities` |
| 角色 | Builder / Admin |

### 2.2 功能方案

| 功能ID | 功能名称 | 功能描述 | 优先级 | 来源 |
|---|---|---|---|---|
| FEAT-04-01 | 能力列表 | 按类型/风险/状态筛选 | P0 | V0.8 / Playbook / 总设 |
| FEAT-04-02 | Capability Contract | Input/Output Schema、风险、副作用、幂等 | P0 | V0.8 / Playbook / 总设 |
| FEAT-04-03 | 四类实现 | Platform Service/HTTP/MCP/Sandbox typed form | P0 | V0.8 / Playbook / 总设 |
| FEAT-04-04 | 自动分页 | Page/Offset/Cursor | P0 | V0.8 / Playbook / 总设 |
| FEAT-04-05 | 测试 | 执行测试并显示结果 | P0 | V0.8 / Playbook / 总设 |

### 2.3 范围与边界

| 类别 | 内容 |
|---|---|
| 范围（In Scope） | 完成 Capability Contract、四类实现配置、Data Retrieval Policy、测试和只读详情。 |
| 非范围（Out of Scope） | 不提供万能 JSON implementation 编辑器，不把 ProjectPlatform FK 强加给 HTTP/MCP/Sandbox。 |
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
| S-04-01 | FEAT-04-01 | E2E | 四类实现动态表单 | Builder 分别创建 HTTP/MCP/PLATFORM_SERVICE/SANDBOX 能力 | 类型切换渲染对应字段；PLATFORM_SERVICE 必选平台（安全选项） |
| S-04-02 | FEAT-04-02 | E2E | 分页策略表单 | Builder 配置 Page 分页+短页终止 | short_page_terminates 默认开启；可显式关闭并提示适用前提（R19 语义） |
| S-04-03 | FEAT-04-04 | E2E | 能力测试 | Admin 测试能力 | 测试结果展示 inline/summary；失败显示脱敏错误 |
| E-04-01 | FEAT-04-03 | E2E | 副作用字段必填 | 创建能力不选"是否有副作用" | 校验失败；选 true 时风险等级建议提升提示 |
| E-04-01 | FEAT-04-01 | integration | services → API → UI | 后端返回字段校验/权限/冲突错误 | 保留当前上下文并显示可定位错误，不出现假成功 |

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
| 能力管理 | `/console/capabilities` | ConsoleLayout | 完成 Capability Contract、四类实现配置、Data Retrieval Policy、测试和只读详情。 |

### 3.3 组件设计

| 组件ID | 组件名 | 类型 | 职责 |
|---|---|---|---|
| CMP-04-01 | CapabilityListPage | 容器 | 标准列表 |
| CMP-04-02 | CapabilityForm | 容器 | 公共 Contract 字段 |
| CMP-04-03 | ImplementationDiscriminator | 容器 | 按类型切换字段 |
| CMP-04-04 | DataRetrievalPolicyForm | 容器 | 分页策略 |
| CMP-04-05 | CapabilityTestPanel | 容器 | 测试输入/结果 |

### 3.4 组件接口契约与字段

**公共字段**：名称、标识、说明、Input Schema、Output Schema、风险等级、副作用、幂等性、实现类型（创建后不可改）、状态。

**Platform Service**：项目平台、服务名、操作/接口、认证模式、timeout/retry、自动分页。
**HTTP**：Method、Base URL/URL、Path、Request Mapping、非 Secret Headers、共享认证、timeout/retry、自动分页；不显示 ProjectPlatform。
**MCP**：Server/Config、Tool name、共享认证、timeout、参数映射；不显示 ProjectPlatform。
**Sandbox**：受控 entrypoint、参数模板、workspace policy、CPU/内存/输出/network/env 限制；禁止自由 shell textarea。

**Pagination**：Page/Offset/Cursor 各自字段 + items_path + total/has_more/next_cursor + max_pages/max_items/max_duration + duplicate-page detection；Page/Offset `items==[]` 固定兜底；`short_page_terminates` 默认开启（true），下游可能产生中间短页时必须允许显式关闭；保证“非最后页必满”才可安全依赖短页终止（V1.12 裁决）。



**平台选择与安全**：PLATFORM_SERVICE 的项目平台下拉使用 PLAT-API-01 的安全只读 DTO，显示 name/key/configured/enabled；UNCONFIGURED 不能用于测试/启用运行并显示“待配置认证模板”。Builder 可选择已配置平台，但不能打开认证管理写操作。

### 3.5 状态与数据流

```text
用户操作
→ 容器组件校验
→ services/04Service
→ 后端 API
→ 统一错误映射
→ query/local state
→ UI 重渲染
```

**API 映射**

| Service 方法/动作 | API ID | 方法/路径或契约 | 后端 Owner |
|---|---|---|---|
| Capability 列表 | `CAP-API-01` | `GET /api/v1/capabilities` | Capability Runtime |
| 新增 Capability | `CAP-API-02` | `POST /api/v1/capabilities` | Capability Runtime |
| Capability 详情 | `CAP-API-03` | `GET /api/v1/capabilities/{capability_id}` | Capability Runtime |
| 编辑 Capability | `CAP-API-04` | `PUT /api/v1/capabilities/{capability_id}` | Capability Runtime |
| 测试 Capability | `CAP-API-05` | `POST /api/v1/capabilities/{capability_id}/test` | Capability Runtime |
| 读取 Capability Contract | `CAP-API-06` | `GET /api/v1/capabilities/{capability_id}/contract` | Capability Runtime |

### 3.6 UI 状态

| 视图/交互 | loading | empty | error | success |
|---|---|---|---|---|
| 列表 | Skeleton | 暂无能力 | 错误+重试 | 列表 |
| 动态实现表单 | 字段 schema loading | — | 字段校验定位 | typed form |
| 测试 | 按钮 loading | 无测试 | 错误详情 | 结果/Artifact |

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
| RISK-04-01 | 实现类型切换造成无效字段残留 | 高 | discriminated form + DTO 类型收敛 | E2E/Integration |
| RISK-04-02 | 大结果直接塞入浏览器/LLM | 高 | 详情显示 Artifact/摘要策略 | E2E/Integration |

## Spec Compliance Matrix

| Spec/Rule | enforcement | 设计影响 | 设计落点 | 验证场景 | 状态/N/A 理由 |
|---|---|---|---|---|---|
| Console-V0.8#READONLY-DETAIL | required | 详情不得变成编辑入口 | §3.3/§3.7 | S-04-01 | applied |
| Console-V0.8#SERVICE-LAYER | required | API 统一从 services 层发起 | §3.5 | S-04-01 | applied |

## 附录：后端追溯

该模块只定义前端状态与交互，不重复定义 DB。DB 字段/索引/约束以对应后端模块 `design-full.md` 为唯一事实源；本文件通过 API ID 追溯到后端 Owner。
