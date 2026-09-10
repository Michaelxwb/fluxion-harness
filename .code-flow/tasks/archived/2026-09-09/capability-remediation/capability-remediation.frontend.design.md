# 能力体系优化补救·前端设计（Console）

> **文档编号**: FE-CAP-REM-v0.1
> **文档版本**: v0.1（草稿）
> **创建日期**: 2026-09-09
> **文档状态**: 草稿
> **来源**: V4 §20/§27/§51-56 + 评审结论；对接后端设计 `capability-remediation.backend.design.md`
> **需求目录**: `.code-flow/tasks/2026-09-09/capability-remediation/`

**评审边界说明**:
- **需求评审**: 第 2 章 → 通过后锁定需求基线
- **设计评审**: 第 3 章 → 通过后锁定设计基线

**ID 体系**: US / FEAT / CMP / NFR；场景 S- / E- / B-

---

## 1. 文档控制

### 1.1 责任人

| 角色 | 姓名 | 职责范围 |
|------|------|---------|
| 开发负责人 | | 技术方案、代码实现 |
| 设计/交互 | | 视觉与交互稿（如有） |

### 1.2 修订历史

| 版本 | 日期 | 作者 | 变更描述 |
|------|------|------|---------|
| v0.1 | 2026-09-09 | | 初始草稿 |

---

## 2. 需求分析

### 2.1 需求概述

| 项目 | 内容 |
|------|------|
| **模块名称** | 能力页面重构（Skills / Tools / MCP 统一信息架构） |
| **需求类型** | 重构 |
| **业务背景** | 现状 `pages/capabilities/` 已有 CapabilitiesPage、各 Editor/Modal，但 Skill 仍是 instructions 文本编辑、MCP 保留 stdio 表单、Tool 类型展示不完整；V4 要求统一三类能力的列表/新增/解析/发布交互 |
| **核心目标** | 三类能力共用一套列表 + 新增 + 发布交互规范，与后端 Published-only、Package 化、删 stdio 对齐 |

### 2.2 功能方案

| 功能ID | 功能名称 | 功能描述 | 优先级 | 来源 |
|--------|---------|---------|--------|------|
| FEAT-F01 | 统一能力信息架构 | 顶级"能力"菜单下 Skills \| Tools \| MCP 三 Tab，共用列表规范（名称/版本/状态/引用数/更新时间/操作） | P0 | V4 §51-54 |
| FEAT-F02 | Skill Package 上传交互 | 新增 Skill = 上传 ZIP → 解析结果预览（manifest/SKILL.md/knowledge 完整度）→ 保存 Draft；删除纯 instructions Editor 创建路径 | P0 | V4 §20/§23 + 后端 FEAT-07 |
| FEAT-F03 | MCP 表单收口 | 删除连接方式/stdio/command/args/cwd/env 表单项；仅保留服务地址+凭据选择+超时+allowed_tools（discover 后选择）；删除 headers 输入 | P0 | 后端 FEAT-02/05 |
| FEAT-F04 | Tool 类型完整呈现 | 列表展示类型（HTTP API / Platform Service）+ 风险等级 + 副作用；新增/编辑表单按 kind 切换字段；测试调用按钮走 `:test` 接口 | P1 | V4 §53 + 后端 FEAT-04/06 |
| FEAT-F05 | 发布状态交互 | Draft / Published / Deprecated 状态徽标；发布/弃用二次确认（含影响说明：引用 Agent 数）；Published 只读 Drawer | P0 | 后端 FEAT-09 |
| FEAT-F06 | 删除与引用保护 | 删除 Draft 直接删；Published 禁止删除只可弃用；删除/弃用前展示引用 Agent 数 | P1 | V4 §56 |

### 2.3 范围与边界

| 类别 | 内容 |
|------|------|
| **范围（In Scope）** | FEAT-F01~F06；`pages/capabilities/` 下现有页面/Modal 重构；`services/` API Client 新增接口 |
| **非范围（Out of Scope）** | Package 在线编辑；Chat Web 改动；后端接口（见后端设计） |
| **有意妥协 / 技术债** | 无 |

### 2.4 验收条件

**正常场景**

| 场景ID | 功能ID | 优先级 | 测试层级 | 关键真实边界 | 操作步骤 | 预期 UI 结果 |
|--------|--------|--------|---------|-------------|---------|-------------|
| S-F01 | FEAT-F02 | P0 | E2E | Browser → Router → Service → UI | 上传合法 ZIP | 解析结果预览正确，保存 Draft 成功 |
| S-F02 | FEAT-F05 | P0 | E2E | Browser → 发布接口 → 状态刷新 | 发布一个 Draft | 状态变为 Published，详情只读 |
| S-F03 | FEAT-F04 | P1 | E2E | Browser → :test 接口 → 结果渲染 | 对 HTTP Tool 点测试调用 | 返回结果正确渲染 |

**异常场景**

| 场景ID | 功能ID | 测试层级 | 关键真实边界 | 触发条件 | UI 表现 |
|--------|--------|---------|-------------|---------|---------|
| E-F01 | FEAT-F02 | integration | Service → UI | manifest 缺失的 ZIP | 字段级错误定位，不生成 Draft |
| E-F02 | FEAT-F03 | integration | Service → UI | 表单含 headers/stdio 字段 | 该字段不存在（回归断言） |
| E-F03 | FEAT-F06 | integration | Service → UI | 删除被引用的 Published | 按钮禁用 + 引用数提示 |

**非功能指标**

| 指标ID | 指标名称 | 目标值 | 测量方法 |
|--------|---------|-------|---------|
| NFR-F01 | Console 资源列表/详情 | P95 ≤ 300ms | 基线压测（AGENTS.md） |

---

## 3. 前端技术设计

### 3.1 技术选型

| 类别 | 选型 | 版本 | 选型理由 |
|------|------|------|---------|
| 框架 | React + TypeScript + Vite | 19 | 现状 |
| 组件库 | @douyinfe/semi-ui + semi-icons | 2.102.x | 强制规范（AGENTS.md #20）；入口首导入 react19-adapter |
| 数据请求 | services/ API Client | 现状 | 组件禁止裸 fetch |

### 3.2 页面与路由结构

| 页面 | 路由 | 说明 |
|------|------|------|
| 能力总览 | `/capabilities` | 三 Tab：Skills \| Tools \| MCP（复用 CapabilitiesPage） |
| Skill 新增（上传） | Modal | ZIP 上传 + 解析预览（改造 CreateSkillModal） |
| Skill 详情 | Drawer 只读 | Published 只读（含 artifact hash） |
| MCP 编辑 | `/capabilities/mcps/:id` | 收口后表单（改造 McpEditorPage） |
| Tool 新增/编辑 | Modal | 按 kind 切换字段 |

### 3.3 组件设计

| 组件ID | 组件 | 职责 | 复用 |
|--------|------|------|------|
| CMP-01 | CapabilityTabs | 三 Tab 切换 + 统一列表规范 | 现状 CapabilitiesPage 改造 |
| CMP-02 | PackageUploadModal | ZIP 上传 + 解析结果预览 + Draft 保存 | 新增（F02） |
| CMP-03 | McpForm | 无 stdio/headers 的收口表单 + discover 选择 allowed_tools | 改造 McpEditorPage/CreateMcpServerModal |
| CMP-04 | ToolForm | kind 切换（http_api/platform_service）+ 测试调用按钮 | 新增/改造 |
| CMP-05 | PublishConfirm | 发布/弃用二次确认（含引用数影响说明） | 新增（F05/F06） |
| CMP-06 | StatusBadge | DRAFT/PUBLISHED/DEPRECATED 徽标 | 新增 |

### 3.4 组件接口契约

| 组件 | Props | Events |
|------|-------|--------|
| PackageUploadModal | `open: boolean` | `onSuccess(draft)` / `onError(fieldErrors)` |
| McpForm | `initial?: McpDraft` | `onDiscover(tools)` / `onSubmit(values)` |
| ToolForm | `kind: 'http_api' \| 'platform_service'` | `onTest(result)` / `onSubmit(values)` |
| PublishConfirm | `refCount: number` | `onConfirm()` / `onCancel()` |

### 3.5 状态与数据流

- 列表数据经 `services/` API Client 获取（含 envelope `{code,message,data,request_id}` 解包），组件内只保留分页/筛选 UI 状态。
- 表单校验错误由后端字段级错误驱动定位（E-F01）。
- Published 详情 Drawer 只读，不设编辑态。

### 3.6 UI 状态

| 场景 | loading | empty | error | success |
|------|---------|-------|-------|---------|
| 列表 | Table loading | 空态插画+新建引导 | 错误提示+重试 | — |
| 上传解析 | 上传进度 | — | 字段级错误 | 解析预览 |
| 发布/弃用 | 按钮 loading | — | Toast | 状态徽标更新 |

### 3.7 样式方案

沿用 Console 主题 token（`theme.ts`）与 Semi Design；禁止引入第二套组件库；高风险操作（发布/回滚/删除/弃用）必须有明确确认和影响说明。

---

## 4. 风险与依赖

| 风险ID | 类型 | 描述 | 概率 | 影响 | 应对措施 | 验证场景 |
|--------|------|------|------|------|---------|---------|
| RISK-F01 | 依赖 | 后端接口未就绪（:test/:discover/tool-policies） | 中 | 高 | 按 §3.4 契约并行，先 mock 联调后切真接口 | S-F03 |
| RISK-F02 | 回归 | 旧 Editor 入口残留 | 低 | 中 | 删除 instructions Editor 路由 + 回归断言 E-F02 | E-F02 |

---

## 附录：术语表

| 术语 | 定义 |
|------|------|
| CMP | Component，组件 |
| Drawer | Semi 抽屉组件（详情只读） |

---

*文档结束*
