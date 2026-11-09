---
design_template: frontend
requirement: user-skill-sandbox
---

# 用户自建 Skill 与统一沙箱执行 前端模块需求与设计

> **文档编号**: FE-SKILL-SANDBOX-001
> **文档版本**: v0.1
> **创建日期**: 2026-11-06
> **文档状态**: 草稿（设计评审中）

**评审边界说明**:
- **需求评审**: 第 2 章（需求分析）→ 通过后锁定需求基线
- **设计评审**: 第 3 章（前端技术设计）→ 通过后锁定设计基线

**ID 体系**: US（来自 PRD）、FEAT（功能）、CMP（组件）、NFR（非功能指标）
场景源：`user-skill-sandbox.scenarios.md`。

---

## 目录

- [1. 文档控制](#1-文档控制)
- [2. 需求分析](#2-需求分析)
  - [2.1 需求概述](#21-需求概述-必填)
  - [2.2 功能方案](#22-功能方案-必填)
  - [2.3 范围与边界](#23-范围与边界-必填)
  - [2.4 验收条件](#24-验收条件-必填)
- [3. 前端技术设计](#3-前端技术设计)
  - [3.1 技术选型](#31-技术选型-必填)
  - [3.2 页面与路由结构](#32-页面与路由结构-必填)
  - [3.3 组件设计](#33-组件设计-必填)
  - [3.4 组件接口契约](#34-组件接口契约-必填)
  - [3.5 状态与数据流](#35-状态与数据流-必填)
  - [3.6 UI 状态](#36-ui-状态-必填)
  - [3.7 样式方案](#37-样式方案-必填)
  - [3.8 可访问性与兼容性](#38-可访问性与兼容性-按需)
- [4. 风险与依赖](#4-风险与依赖)
- [Spec Compliance Matrix](#spec-compliance-matrix)
- [附录：术语表](#附录术语表)

---

## 1. 文档控制

### 1.1 责任人

| 角色 | 姓名 | 职责范围 |
|------|------|---------|
| 开发负责人 | 待定 | 前端技术方案、组件实现 |
| 设计/交互 | 待定 | 交互稿、视觉验收 |

### 1.2 修订历史

| 版本 | 日期 | 作者 | 变更描述 |
|------|------|------|---------|
| v0.1 | 2026-11-06 | OpenCode Agent | 基于 PRD 与现有 `/skills` 页面形成前端设计初稿 |

---

## 2. 需求分析

### 2.1 需求概述 [必填]

| 项目 | 内容 |
|------|------|
| **模块名称** | Console Skill 管理（用户自建与审批） |
| **需求类型** | 现有页面扩展 + 新增审批交互 |
| **业务背景** | 用户可自建 Skill 后，管理员需要在 Console 查看待审包、审批/驳回、下载、管理用户范围和 Agent 绑定、解绑+软删；现有 `/skills` 只支持管理员导入和基础列表，缺少 source/审批状态和审批操作。 |
| **核心目标** | 在不新建独立页面的前提下，把 `/skills` 扩展为“系统/管理员/用户 Skill 的统一管理入口”，让管理员能完成审批、复查、下载和撤销。 |

### 2.2 功能方案 [必填]

| 功能ID | 功能名称 | 功能描述 | 优先级 | 来源 |
|--------|---------|---------|--------|------|
| FEAT-12 | 管理员 Skill 管理界面与接口 | `/skills` 增加 source/审批筛选、审批抽屉、下载、解绑软删、系统 Skill 上传；保留现有 artifact `platforms` 列/详情展示 | P0 | US-13, US-14, US-15, US-16 |
| FEAT-13 | 系统 Skill 上传 | 导入弹窗增加 `source=SYSTEM`，自动通过与全局生效 | P0 | US-17 |
| FEAT-05 | 已审批包下载与审计 | 审批抽屉/详情提供下载按钮，下载失败有明确提示 | P0 | US-14 |
| FEAT-14 | 审计/错误提示 | 审批/下载/删除结果显示，错误码有双语提示 | P0 | US-19, US-21 |

> 用户草稿创建、写文件、`skill_draft_shell` 通过对话工具完成，不在本前端模块新增独立页面；本设计只覆盖管理员 Console 界面。

### 2.3 范围与边界 [必填]

| 类别 | 内容 |
|------|------|
| **范围（In Scope）** | `/skills` 列表扩展；审批抽屉；批准/驳回；下载；用户范围/grant；Agent 绑定；解绑+软删二次确认；系统 Skill 上传；保留现有 artifact `platforms` 列/详情 hint；空态/错误/无权限；i18n；可访问性。 |
| **非范围（Out of Scope）** | 用户“我的 Skill”独立页面；草稿编辑器/文件树；在线代码编辑；`workspace_shell` 前端终端；Skill 市场/评分/版本回滚；移动端专门适配；系统依赖预装 UI（本期由管理员通过现有镜像构建清单完成，另开需求再做页面）；**Skill 表新增平台关联字段**（现有 artifact frontmatter `platforms` 展示保留）。 |
| **有意妥协 / 技术债** | 审批抽屉复用现有 `DetailSideSheet`/`Tabs` 结构，不重新设计一套详情框架；用户草稿状态继续通过对话工具 `skill_draft_status` 查看，不在 Console 增加通知中心。 |

### 2.4 验收条件 [必填]

> 完整场景定义见 `user-skill-sandbox.scenarios.md`；以下只列前端设计影响。

| 场景ID | 前端设计影响 / 备注 |
|--------|--------------------|
| S-35 | 审批抽屉展示包信息、校验结果、提交者/Agent、审批意见；批准/驳回按钮状态 |
| S-36 | 解绑+软删二次确认弹窗，展示将解绑的 Agent 列表 |
| S-37 | `/skills` source/审批状态筛选、pending 标记、分页组合 |
| E-13 | 空态/错误/无权限状态，不只靠颜色 |
| S-39 | 管理员在真实浏览器完成审批 E2E |
| S-40 | 管理员在真实浏览器完成解绑+软删 E2E |
| S-07 | 下载按钮与失败提示 |
| E-12 | 非管理员操作隐藏/禁用与接口 403 处理 |

#### 2.4.1 验证环境假设 [必填]

| 维度 | 假设 | 验证方式 / 参数 | 不满足时 |
|------|------|----------------|---------|
| 容量 | `/skills` 默认 15/页；source/状态筛选组合 | RemoteTable fixture + Playwright | 标记 `incomplete` |
| 硬件 | CI Chrome/Chromium，桌面视口 | Playwright project `chrome` | `env_blocked` |
| 依赖 | Console 后端可用；管理员/普通用户测试账号；真实 PG | 域测试 preflight | `env_blocked` |
| profile 参数 | component 测试 mock API；E2E 使用真实后端 | `npm run test`/`npm --prefix e2e test` | 显式报 `incomplete` |

---

## 3. 前端技术设计

### 3.1 技术选型 [必填]

| 类别 | 选型 | 版本 | 选型理由 |
|------|------|------|---------|
| 框架 | React | 18 | 现有 Console |
| 语言 | TypeScript | 5.7 | 现有类型约束 |
| 组件库 | Semi Design | 现有 | 现有列表/表单/SideSheet/Tag |
| 路由 | React Router | 现有 | `/skills` 已存在，不新增路由 |
| 状态管理 | 组件局部 state + `useCallback`/`useEffect` | 现有 | 页面级状态简单，无需引入全局 store |
| 数据请求 | `src/api/client.ts` + `modules/skill-management/services/skills.ts` | 现有 | 遵守 RULE-front-001 |
| 国际化 | react-i18next | 现有 | 新增词条 zh-CN/en-US |

### 3.2 页面与路由结构 [必填]

| 页面 | 路由 | 布局 | 说明 |
|------|------|------|------|
| Skill 管理 | `/skills` | 现有 `PageHeader + PageSection + ModuleToolbar + RemoteTable` | 扩展筛选/列/操作，不新增页面 |
| Skill 详情/审批 | `/skills`（SideSheet） | `DetailSideSheet` + `Tabs` | 复用现有详情抽屉，增加“审批”Tab 或审批区 |

**筛选参数**（URL query 可选，便于从待审提醒直达）：

```text
/skills?source=USER&review_status=PENDING&page=1
```

- `source`: `SYSTEM` / `ADMIN` / `USER`
- `review_status`: `PENDING` / `APPROVED` / `REJECTED` / `SUPERSEDED`
- 保持现有 `keyword`、`user_scope`、`enabled`、`execution_mode`。

### 3.3 组件设计 [必填]

**组件树**

```text
<SkillPage>                         # 容器：列表参数、数据加载、刷新
├─ <ModuleToolbar>                  # 现有：搜索 + 筛选 + 导入
│  ├─ <SkillSourceFilter>           # 新增：source Select
│  ├─ <SkillReviewStatusFilter>     # 新增：审批状态 Select
│  └─ <SkillImportButton>           # 现有：导入/系统 Skill 上传
├─ <RemoteTable>                    # 现有：列表
│  ├─ <SkillSourceTag>              # 新增：SYSTEM/ADMIN/USER 标签
│  ├─ <SkillReviewStatusTag>        # 新增：PENDING/APPROVED/REJECTED/SUPERSEDED
│  ├─ <SkillPlatforms>              # 现有：artifact frontmatter platforms 展示，保留
│  └─ <SkillRowActions>             # 现有操作列 + 审批/下载/删除
├─ <SkillDetailSideSheet>           # 现有：详情/包/Agent 绑定
│  ├─ <SkillReviewPanel>            # 新增：审批信息 + 操作
│  ├─ <SkillArtifactDownloadButton> # 新增：下载
│  └─ <SkillDeleteButton>           # 新增：解绑+软删
├─ <SkillReviewDrawer>              # 新增：待审包审批专用（可复用 SideSheet）
│  ├─ <SkillArtifactSummary>        # 新增：checksum/大小/文件数/校验结果
│  ├─ <SkillFileList>               # 新增：文件清单（只读）
│  ├─ <SkillSubmitterInfo>          # 新增：提交者/渠道/上传 Agent
│  └─ <SkillReviewActions>          # 新增：批准/驳回 + review_note
├─ <SkillDeleteConfirm>             # 新增：二次确认 + 解绑 Agent 列表
└─ <SkillImportModal>               # 现有：增加 source=SYSTEM/ADMIN
```

| 组件ID | 组件名 | 类型 | 复用来源/去向 | 职责 |
|--------|--------|------|--------------|------|
| CMP-01 | `SkillPage` | 容器 | 现有 | 列表参数、加载、刷新、筛选 |
| CMP-02 | `SkillSourceFilter` | 展示 | 新建 | source 筛选 |
| CMP-03 | `SkillReviewStatusFilter` | 展示 | 新建 | 审批状态筛选 |
| CMP-04 | `SkillSourceTag` | 展示 | 新建 | source 标签/颜色 |
| CMP-05 | `SkillReviewStatusTag` | 展示 | 新建 | 审批状态标签 |
| CMP-06 | `SkillReviewPanel` | 容器 | 新建 | 审批信息 + 操作 |
| CMP-07 | `SkillReviewActions` | 展示 | 新建 | 批准/驳回按钮、review_note |
| CMP-08 | `SkillFileList` | 展示 | 新建 | 包内文件/大小清单 |
| CMP-09 | `SkillDeleteConfirm` | 展示 | 新建 | 解绑 Agent 列表 + 二次确认 |
| CMP-10 | `SkillArtifactDownloadButton` | 展示 | 新建 | 下载 + 失败提示 |
| CMP-11 | `SkillImportModal` | 容器 | 现有扩展 | 增加 source 选择 |
| CMP-12 | `SkillDetailSideSheet` | 容器 | 现有扩展 | 增加审批/下载/删除入口 |

> 容器负责数据获取与状态；展示组件只接收 props、通过事件上抛。审批/删除 API 调用集中在 `services/skills.ts`。

### 3.4 组件接口契约 [必填]

**CMP-06 `SkillReviewPanel`**

| Props | 类型 | 必填 | 默认 | 说明 |
|-------|------|------|------|------|
| `skillId` | string | 是 | - | Skill ID |
| `artifact` | `SkillArtifactDetail` | 是 | - | 当前待审 artifact |
| `submitter` | `SkillSubmitterInfo` | 是 | - | 提交者/渠道/Agent |
| `canReview` | boolean | 是 | - | 管理员权限 |
| `onReviewed` | `(action: 'APPROVE' \| 'REJECT') => void` | 是 | - | 审批完成回调 |
| `onDownload` | `() => void` | 是 | - | 下载回调 |

**CMP-07 `SkillReviewActions`**

| Props | 类型 | 必填 | 默认 | 说明 |
|-------|------|------|------|------|
| `mode` | `'APPROVE' \| 'REJECT'` | 是 | - | 当前操作 |
| `note` | string | 否 | `''` | 驳回原因 |
| `submitting` | boolean | 否 | false | 提交中 |
| `onNoteChange` | `(value: string) => void` | 是 | - | 输入回调 |
| `onSubmit` | `() => void` | 是 | - | 提交回调 |
| `onCancel` | `() => void` | 是 | - | 取消 |

**CMP-09 `SkillDeleteConfirm`**

| Props | 类型 | 必填 | 默认 | 说明 |
|-------|------|------|------|------|
| `skill` | `SkillListItem` | 是 | - | 目标 Skill |
| `agents` | `SkillAgentItem[]` | 是 | - | 将被解绑的 Agent |
| `visible` | boolean | 是 | - | 是否显示 |
| `confirmLoading` | boolean | 否 | false | 删除中 |
| `onConfirm` | `() => void` | 是 | - | 二次确认 |
| `onCancel` | `() => void` | 是 | - | 取消 |

**CMP-10 `SkillArtifactDownloadButton`**

| Props | 类型 | 必填 | 默认 | 说明 |
|-------|------|------|------|------|
| `skillId` / `artifactId` | string | 是 | - | 下载目标 |
| `disabled` | boolean | 否 | false | 无权限/包损坏 |
| `onDownloaded` | `() => void` | 否 | - | 成功回调 |
| `onError` | `(code: string) => void` | 否 | - | 错误回调 |

**CMP-11 `SkillImportModal` 扩展**

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `source` | `'SYSTEM' \| 'ADMIN'` | 是 | 仅管理员可见；默认 `ADMIN`；选择 SYSTEM 时提示“自动通过、全局生效” |
| 其他字段 | 现有 | - | version/key/default_script/user_scope 保持 |

### 3.5 状态与数据流 [必填]

**状态划分**

| 状态 | 作用域 | 形状 | 读写方 |
|------|--------|------|--------|
| 列表参数 | `SkillPage` local | `{page,page_size,keyword,source,review_status,user_scope}` | 筛选/分页/刷新 |
| 列表数据 | `SkillPage` local | `SkillListItem[]`, `total`, `loading`, `failed` | `RemoteTable` |
| 详情/审批 | `SkillDetailSideSheet` local | `SkillDetail`, `SkillArtifactDetail[]` | 详情/审批 |
| 审批表单 | `SkillReviewPanel` local | `{mode, note, submitting, error}` | 审批操作 |
| 删除确认 | `SkillPage` local | `{target, agents, loading}` | 删除流程 |

**数据流**

```text
筛选/分页 → SkillPage params → services.listSkills() → items/total → RemoteTable
行操作“审批” → getSkill + listArtifacts → SkillDetailSideSheet/SkillReviewPanel
批准/驳回 → services.reviewSkill() → 刷新列表/详情 → Toast + 审计
删除 → listSkillAgents → SkillDeleteConfirm → services.deleteSkill({unbind:true}) → 刷新
下载 → services.downloadArtifact() → Blob/URL → 浏览器下载
```

**数据获取层**

| Service 方法 | 对应后端接口 | 调用方组件/hook |
|-------------|-------------|----------------|
| `listSkills(params)` | `GET /api/v1/skills` | `SkillPage` |
| `getSkill(id)` | `GET /api/v1/skills/{id}` | `SkillDetailSideSheet` |
| `listArtifacts(id, params)` | `GET /api/v1/skills/{id}/artifacts` | `SkillDetailSideSheet` |
| `reviewSkill(id, payload)` | `POST /api/v1/skills/{id}/review` | `SkillReviewPanel` |
| `downloadArtifact(skillId, artifactId)` | `GET .../download` | `SkillArtifactDownloadButton` |
| `deleteSkill(id, {unbind:true})` | `DELETE /api/v1/skills/{id}?unbind=true` | `SkillDeleteConfirm` |
| `listSkillAgents(id, params)` | `GET /api/v1/skills/{id}/agents` | `SkillDeleteConfirm` |
| `importSkill({source,...})` | `POST /api/v1/skills/import` | `SkillImportModal` |
| `setUserScope` / `addGrant` / `removeGrant` | 现有 | `SkillScopeModal`/`SelectedUserTable` |

**类型扩展**

```ts
export type SkillSource = 'SYSTEM' | 'ADMIN' | 'USER';
export type SkillReviewStatus = 'PENDING' | 'APPROVED' | 'REJECTED' | 'SUPERSEDED';

export interface SkillListItem {
  // 现有字段...
  platform_label: string | null;
  platforms: string[];
  source: SkillSource;
  author: string | null;
  review_status: SkillReviewStatus | null;
}

export interface SkillArtifactDetail {
  // 现有字段...
  platforms: string[];
  review_status: SkillReviewStatus;
  review_note: string | null;
  reviewed_by: string | null;
  reviewed_at: string | null;
  submitted_by_user_id: string | null;
  submitted_agent_id: string | null;
  submitted_channel: 'WECOM' | 'WEBCHAT' | null;
}
```

### 3.6 UI 状态 [必填]

| 视图/交互 | loading | empty | error | success |
|----------|---------|-------|-------|---------|
| `/skills` 列表 | 表格 loading | “暂无 Skill” + 导入入口 | 错误提示 + 重试，保留筛选 | 列表 + 分页 |
| 审批抽屉 | Spin | “该 Skill 没有待审包” | 错误 + 重试 | 包信息 + 批准/驳回 |
| 下载 | 按钮 loading | - | 明确“包不存在/校验失败/无权限” | 浏览器下载 + Toast |
| 解绑+软删 | 确认按钮 loading | 无绑定 Agent 时提示“仅软删” | 错误 + 重试/取消 | 列表刷新 + Toast |
| 无管理员权限 | - | - | 审批/删除/系统上传隐藏或禁用，接口 403 有提示 | 只读详情 |
| 筛选无结果 | - | “没有匹配的 Skill” + 清除筛选 | - | - |

**审批状态展示**

| 状态 | 标签颜色 | 文案 key |
|------|---------|----------|
| PENDING | orange | `skill.review.status.pending` |
| APPROVED | green | `skill.review.status.approved` |
| REJECTED | red | `skill.review.status.rejected` |
| SUPERSEDED | grey | `skill.review.status.superseded` |

> 状态不能只用颜色表达，标签同时带文字；`aria-label` 包含状态文案。

### 3.7 样式方案 [必填]

| 维度 | 约定 |
|------|------|
| **样式与逻辑分离** | 复用现有 `PageSection`/`ModuleToolbar`/`DetailSideSheet`/`DetailGrid`；新增样式走 CSS class/tokens，不在组件内写业务计算 |
| **设计 tokens** | 间距/颜色/字号使用 Semi token/现有 CSS 变量；不散落魔法值 |
| **字段布局排版** | 审批摘要用 `DetailGrid`；文件清单用现有表格样式；审批意见用 `Form.TextArea`，双列/单列按现有表单规范 |
| **响应式断点** | 跟随 Console 现有桌面布局；SideSheet 宽度使用现有 `DetailSideSheet` 默认；窄屏下按钮换行不重叠 |
| **危险操作** | 删除按钮使用 danger 主题；二次确认弹窗展示影响范围（Agent 列表），确认按钮文案明确“解绑并删除” |

### 3.8 可访问性与兼容性 [按需]

| 维度 | 要求 |
|------|------|
| 可访问性 | 表格/标签/按钮语义正确；审批抽屉焦点进入/返回；`aria-label` 包含 Skill 名和状态；键盘可完成批准/驳回/取消 |
| 浏览器/设备兼容 | Console 现有支持的 Chrome/Edge 桌面版本；Playwright 使用 `channel: 'chrome'` |
| 国际化 | 新增词条 zh-CN/en-US；错误码文案来自后端 `msg`，前端兜底 key |
| 破坏性操作 | 解绑+软删必须二次确认；确认前不调用删除 API |

---

## 4. 风险与依赖

| 风险ID | 描述 | 影响 | 应对 | 验证场景 |
|--------|------|------|------|---------|
| FE-RISK-01 | `/skills` 列表字段/筛选增加后旧测试快照失败 | 回归噪声 | 更新 component 测试和 i18n 快照 | S-37 |
| FE-RISK-02 | 审批抽屉与现有详情抽屉状态不同步 | 用户看到旧状态 | 审批成功后 `reloadKey` + 列表刷新；统一 `onMutated` | S-35, S-39 |
| FE-RISK-03 | 删除确认未展示全部绑定 Agent | 误删影响范围不清 | 删除前 `listSkillAgents` 拉全量/分页汇总；确认弹窗展示数量+列表 | S-36, S-40 |
| FE-RISK-04 | 无权限用户仍看到操作入口 | 误导/越权尝试 | 前端按 `canReview` 隐藏/禁用，后端 403 兜底 | E-12 |
| FE-RISK-05 | 下载大包阻塞 UI | 页面卡顿 | 使用浏览器下载/Blob，按钮 loading，不阻塞列表 | S-07 |

---

## Spec Compliance Matrix

| Spec/Rule | enforcement | 设计影响 | 设计落点 | 验证场景 | 状态/N/A 理由 |
|-----------|-------------|---------|---------|---------|----------------|
| `harness-frontend#RULE-front-001` | required | 所有 HTTP 调用经 `modules/skill-management/services/skills.ts` 和 `api/client`，组件不裸用 axios/fetch | §3.5 数据获取层 | S-35, S-37 | applied |
| `harness-ui#RULE-ui-001` | required | `/skills` 保持“左上操作 + 右上搜索筛选 + 列表 + 右下分页”；主展示字段即详情入口 | §3.2, §3.3 | S-35, S-37, E-13 | applied |
| `harness-ui-detail#RULE-ui-detail-001` | required | 详情/审批用 `DetailSideSheet`，对象操作集中列表最后一列；详情不重复渲染 | §3.2, §3.3 | S-35, S-36, S-39, S-40 | applied |
| `harness-i18n#RULE-i18n-001` | required | 新增 source/审批状态/审批动作/删除确认词条 zh-CN/en-US | §3.6, §3.8 | E-13, E-14 | applied |
| `harness-e2e#RULE-e2e-001` | required | 审批/解绑使用真实浏览器 + 真实后端 + 真实 PG；不使用 mock 业务 API | §2.4, S-39/S-40 | S-39, S-40 | applied |
| `harness-api#RULE-api-001` | required | 前端只消费统一封套；错误提示使用后端 `msg`/错误码 | §3.5, §3.6 | E-14 | applied |
| `harness-auth#RULE-auth-001` | required | 审批/删除/系统上传按管理员权限显示/禁用；后端 403 兜底 | §3.6, §3.8 | E-12 | applied |
| `harness-rel#RULE-rel-001` | required | 用户范围/绑定使用单关系操作；删除走显式解绑事务 | §3.3, §3.4 | S-36, S-40 | applied |

---

## 附录：术语表

| 术语 | 定义 |
|------|------|
| US / FEAT / CMP / NFR | 用户故事 / 功能 / 组件 / 非功能指标 |
| `source` | Skill 来源：SYSTEM/ADMIN/USER |
| `review_status` | PENDING/APPROVED/REJECTED/SUPERSEDED |
| SideSheet | Semi Design 侧边详情抽屉 |
| E2E | 真实浏览器 + 真实后端 + 真实 PG 的端到端测试 |

---

*文档结束*
