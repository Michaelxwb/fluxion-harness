# Skill管理 前端模块需求与设计简报

> **文档编号**: FE-05-V1.11  
> **文档版本**: V1.11 模块分档拆分版  
> **创建日期**: 2026-09-11  
> **文档状态**: 交互基线已冻结，待仓库 Spec Context 绑定  
> **模板**: `design-frontend.md`  
> **交互事实源**: `../90-Console交互规格.md` + `../archive/fluxion-console-interaction-prototype-v0.8-final.html`（已归档：仅作交互形态参考，冲突以 90-规格 + 后端授权列为准）

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
| V1.13 | 2026-09-12 | 第三轮 Review 修复：场景 ID 前缀拆分（E2E 保留 `E-05-01`，integration 改名 `I-05-01`） |

## 2. 需求分析

### 2.1 需求概述

| 项目 | 内容 |
|---|---|
| 模块名称 | Skill管理 |
| 需求类型 | 页面/交互模块 |
| 业务背景 | Skill 已收敛为 IDE 离线开发的 Python 代码资产，Console 不承担开发/编排。 |
| 核心目标 | 只提供 Skill Artifact 导入、校验、版本历史和只读详情；能力依赖来自 manifest。 |
| 路由 | `/console/skills, /console/skills/:id` |
| 角色 | Builder / Admin |

### 2.2 功能方案

| 功能ID | 功能名称 | 功能描述 | 优先级 | 来源 |
|---|---|---|---|---|
| FEAT-05-01 | 导入 Skill | 上传 zip/tar.gz，解析校验预览 | P0 | V0.8 / Playbook / 总设 |
| FEAT-05-02 | 只读详情 | 基本/依赖/Agent/Artifact/校验 | P0 | V0.8 / Playbook / 总设 |
| FEAT-05-03 | 导入新版本 | 不可变 Artifact | P0 | V0.8 / Playbook / 总设 |

### 2.3 范围与边界

| 类别 | 内容 |
|---|---|
| 范围（In Scope） | 只提供 Skill Artifact 导入、校验、版本历史和只读详情；能力依赖来自 manifest。 |
| 非范围（Out of Scope） | 不支持在线 Python 编辑、执行编排、手工绑定/解绑 Capability。 |
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
| S-05-01 | FEAT-05-01 | E2E | 两阶段导入-预览 | Builder 上传 Skill 包（preview） | 展示 Manifest+校验报告；不产生正式记录、不切 current |
| S-05-02 | FEAT-05-01 | E2E | 两阶段导入-确认与取消 | 预览后点确认 / 直接关闭 | 确认后 artifact 落库且 current 切换；取消后无残留（暂存过期） |
| S-05-03 | FEAT-05-03 | E2E | 只读详情 | 查看 Skill 详情与制品版本 | 全只读；仅"导入新版本"入口；校验结果 Tab 展示 validation_report |
| E-05-01 | FEAT-05-01 | E2E | 过期 token 确认 | 预览后等待超时再确认 | 返回 SKILL_PREVIEW_EXPIRED(410) 并引导重新预览 |
| I-05-01 | FEAT-05-01 | integration | services → API → UI | 后端返回字段校验/权限/冲突错误 | 保留当前上下文并显示可定位错误，不出现假成功 |

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
| Skill管理 | `/console/skills, /console/skills/:id` | ConsoleLayout | 只提供 Skill Artifact 导入、校验、版本历史和只读详情；能力依赖来自 manifest。 |

### 3.3 组件设计

| 组件ID | 组件名 | 类型 | 职责 |
|---|---|---|---|
| CMP-05-01 | SkillListPage | 容器 | 列表 |
| CMP-05-02 | SkillImportModal | 容器 | 上传/预览/确认 |
| CMP-05-03 | SkillDetailPage | 容器 | 只读 Tabs |
| CMP-05-04 | ArtifactHistory | 展示 | 版本历史 |
| CMP-05-05 | ValidationReport | 展示 | 校验项 |

### 3.4 组件接口契约与字段

**列表**：名称、标识、platform_label、SDK 版本、entrypoint、依赖能力数、使用智能体数、校验状态、状态、更新时间。

**导入**：唯一输入是本地 `.zip/.tar.gz`；解析后预览 name/key/description/platform_label/sdk_version/entrypoint/capabilities/checksum/校验结果。

**详情 Tabs 全只读**：基本信息、能力依赖（来源 Manifest）、使用智能体（反向查询）、制品版本、校验结果。顶层唯一写操作：`导入新版本`。



**两阶段状态与 DTO**：idle→previewing（multipart mode=preview/artifact）→preview（valid/manifest/validation_report/preview_token/expires_at/checksum）→committing（JSON mode=commit/preview_token）→done（skill_id/artifact_id/revision）。首导和新版本分别调用 SKILL-API-02/04，同一 mode 判别合同。invalid 无 commit token 且确认禁用；取消不调用 commit；过期显示“请重新预览”，依赖/版本变化 409 保留报告并允许重新预览。重复确认禁用按钮且复用 token，成功后只刷新一次详情/current。

**列表查询**：通用契约见 FE-00 §3.4 `StandardListQuery`，本页领域筛选：`platform_label`/`validation_status`/`enabled`（另加 `keyword` 按名称/标识搜索），服务端筛选、改筛选重置 `page=1`、状态进 URL。

### 3.5 状态与数据流

```text
用户操作
→ 容器组件校验
→ services/05Service
→ 后端 API
→ 统一错误映射
→ query/local state
→ UI 重渲染
```

**API 映射**

| Service 方法/动作 | API ID | 方法/路径或契约 | 后端 Owner |
|---|---|---|---|
| Skill 列表 | `SKILL-API-01` | `GET /api/v1/skills` | Skill Runtime |
| 首次导入 Skill | `SKILL-API-02` | `POST /api/v1/skills/import` | Skill Runtime |
| Skill 详情 | `SKILL-API-03` | `GET /api/v1/skills/{skill_id}` | Skill Runtime |
| 导入 Skill 新版本 | `SKILL-API-04` | `POST /api/v1/skills/{skill_id}/artifacts` | Skill Runtime |
| Artifact 历史 | `SKILL-API-05` | `GET /api/v1/skills/{skill_id}/artifacts` | Skill Runtime |
| Skill 能力依赖 | `SKILL-API-06` | `GET /api/v1/skills/{skill_id}/capabilities` | Skill Runtime |
| Skill 使用 Agent | `SKILL-API-07` | `GET /api/v1/skills/{skill_id}/agents` | Skill Runtime |
| Skill 校验结果 | `SKILL-API-08` | `GET /api/v1/skills/{skill_id}/validation` | Skill Runtime |

### 3.6 UI 状态

| 视图/交互 | loading | empty | error | success |
|---|---|---|---|---|
| 列表 | Skeleton | 暂无 Skill | 错误+重试 | 列表 |
| 上传 | progress | 未选择 | 校验错误/警告 | 预览 |
| 详情 | Skeleton | 无历史版本异常 | 加载失败 | 只读 Tabs |

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
| RISK-05-01 | Console 再次出现在线编辑/编排 | 高 | 组件契约不暴露编辑/绑定能力回调 | E2E/Integration |
| RISK-05-02 | manifest 与平台能力不一致 | 高 | 导入时强校验 Capability existence/SDK/entrypoint | E2E/Integration |

## Spec Compliance Matrix

| Spec/Rule | enforcement | 设计影响 | 设计落点 | 验证场景 | 状态/N/A 理由 |
|---|---|---|---|---|---|
| Console-V0.8#READONLY-DETAIL | required | 详情不得变成编辑入口 | §3.3/§3.7 | S-05-01 | applied |
| Console-V0.8#SERVICE-LAYER | required | API 统一从 services 层发起 | §3.5 | S-05-01 | applied |
| BACKEND-08#SKILL-TWO-PHASE-IMPORT | required | Skill 两阶段导入（preview_token + commit，不落正式记录直到确认） | §3.4 | S-05-01 | applied |

## 附录：后端追溯

该模块只定义前端状态与交互，不重复定义 DB。DB 字段/索引/约束以对应后端模块 `design-full.md` 为唯一事实源；本文件通过 API ID 追溯到后端 Owner。
