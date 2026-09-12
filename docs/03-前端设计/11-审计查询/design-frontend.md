# 审计查询（Audit Logs）前端设计

## 1. 文档控制

| 项 | 内容 |
|---|---|
| 模块 | 审计查询（Console 前端模块 11） |
| 角色 | Admin（Builder 不可见菜单） |
| 文档状态 | 交互基线已冻结，待仓库 Spec Context 绑定 |

## 2. 需求分析

### 2.1 需求概述

| 项 | 内容 |
|---|---|
| 核心目标 | 为 Admin 提供发布/绑定/授权/凭据/停用等写操作的只读审计检索，支撑排障与合规回溯（总设 Admin 旅程“Execution / Timeline / Audit”三要素之一）。 |
| 用户与场景 | Admin 定位"谁在什么时候改了什么"、追溯一次发布/授权操作的上下文。 |
| 来源 | 总设 §1.2 Admin 旅程、§6.6 IA（V1.12 补入）、AUDIT-API-01 |

### 2.2 功能方案

| 功能ID | 功能 | 说明 | 优先级 | 来源 |
|---|---|---|---|---|
| FEAT-11-01 | 审计列表 | 分页只读列表 + 多维筛选 | P1 | AUDIT-API-01 |
| FEAT-11-02 | 行展开详情 | 展示 details JSON（脱敏后）与 trace 关联 | P2 | AUDIT-API-01 |

### 2.3 范围与边界

| 类别 | 内容 |
|---|---|
| 范围（In Scope） | 审计事件的只读检索与查看。 |
| 非范围（Out of Scope） | 不提供导出、不提供修改/删除（audit_log 不可变）。 |

### 2.4 验收条件

- Admin 可按时间范围/操作人/资源类型/动作筛选，结果与 audit_log 一致且分页正确（S-11-01）；
- 行展开显示脱敏 details，execution_id 非空时 trace 关联跳转执行记录详情，空时仅显示可复制 trace_id（S-11-02）；
- Builder 无菜单入口且 API 返回 403（E-11-01）。

## 3. 前端技术设计

### 3.1 技术选型

| 类别 | 选型 | 说明 |
|---|---|---|
| 框架 | React 18 + Vite | 沿用 Console 公共框架 |
| 组件库 | Semi Design | 沿用公共列表三件套 |
| 路由 | /console/audit-logs | ConsoleLayout 子路由，Admin-only |

### 3.2 页面与路由结构

| 页面/组件 | 路由/说明 |
|---|---|
| AuditLogListPage | `/console/audit-logs`，Semi Table：只读列表，左上无主操作，右上筛选，右下分页 |

### 3.3 组件设计

| 组件ID | 组件 | 类型 | 职责 |
|---|---|---|---|
| CMP-11-01 | AuditFilterBar | 展示 | 时间范围/操作人/资源类型/动作筛选 |
| CMP-11-02 | AuditTable | 展示 | 只读表格 + 行展开 detail JSON |
| CMP-11-03 | TraceLink | 展示 | execution_id 非空才构造执行详情链接；trace_id 只作标签/复制 |

### 3.4 组件接口契约

**列表**：时间、操作人（用户名/标识）、动作（PUBLISH/BIND/GRANT/CREDENTIAL/DISABLE/DELETE 等）、资源类型（resource_type）、资源标识（resource_id）、结果（SUCCESS/DENIED/FAILED）、trace 关联。

**筛选**：时间范围、操作人、资源类型、动作。

**行展开**：details JSON 只读展示（Secret 已脱敏）。

### 3.5 状态与数据流

services/11AuditService：`fetchAuditLogs(params)` → AUDIT-API-01；列表态 loading/empty/error 统一走公共 ErrorMapper。

### 3.6 UI 状态

| 状态 | 呈现 |
|---|---|
| 加载中 | 表格 skeleton |
| 空态 | “暂无审计事件” |
| 筛选无结果 | 空态 + 清除筛选按钮 |

### 3.7 样式与交互规范

沿用公共框架：时间 `YYYY-MM-DD HH:mm:ss`、不重复大标题、危险操作确认（本页无写操作）。

## 4. 风险与依赖

| 风险/依赖 | 等级 | 说明 |
|---|---|---|
| audit_log 写入覆盖度 | 中 | 各模块写操作必须落审计（模块 15 RULE），否则本页数据不全 |
| AUDIT-API-01 分页性能 | 中 | 时间范围必须强制，避免全表扫描 |

## 5. API 映射

| 交互 | API ID | 路径 | 后端模块 |
|---|---|---|---|
| 审计列表 | `AUDIT-API-01` | `GET /api/v1/audit-logs` | 可观测性与Web公共基础 |

## 6. 验收场景

- S-11-01：Admin 按时间范围+资源类型筛选，列表与 audit_log 数据一致且分页正确；
- S-11-02：行展开显示脱敏 detail；有 execution_id 才可跳执行详情；无执行关联只复制 trace_id；
- E-11-01：Builder 访问 `/console/audit-logs` 无菜单入口且 API 返回 403；
- E-11-02：无数据时显示空态，不报错。

## Spec Compliance Matrix

| 规则 | 状态 | 说明 |
|---|---|---|
| Console-V0.8#READONLY-DETAIL | applied | 全页只读 |
| ADR-021#ROLE-GUARD | applied | 菜单与 API 双重 Admin-only |
