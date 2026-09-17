# Skill 管理与 Artifact 前端模块需求与设计简报

> **文档编号**: FE-SKILL-V1.0  
> **文档版本**: v1.0  
> **创建日期**: 2026-09-17  
> **文档状态**: 设计评审中  
> **模板**: design-frontend.md

## 1. 文档控制

| 项目 | 内容 |
|---|---|
| 模块 | Skill 管理与 Artifact |
| 前端目录 | `apps/console-platform/frontend/src/modules/skill-management/` |
| 公共组件 | `src/components/common/`，由 01-platform-foundation 提供 |
| 交互基线 | 最新 `MSS智能服务交付平台-V1.3-交互稿.html` |

## 2. 需求分析

### 2.1 需求概述

| 项目 | 内容 |
|---|---|
| 模块名称 | Skill 管理与 Artifact |
| 需求类型 | 页面/组件/交互实现 |
| 核心目标 | 复刻最新交互稿的 Skill 信息架构，清晰表达 Artifact 版本和资源级用户范围。 |

### 2.2 功能方案

| 功能ID | 功能名称 | 功能描述 | 优先级 | 来源 |
|---|---|---|---|---|
| FEAT-FE-01 | Skill 列表/导入 | 用户范围、版本、Agent数、指定用户数、启用状态。 | P0 | 需求描述 |
| FEAT-FE-02 | Skill 详情 | 基本信息/版本记录/使用Agent/指定用户 Tabs。 | P0 | 需求描述 |
| FEAT-FE-03 | 版本导入/详情 | Header 导入新版本；版本号可点击查看 manifest/SKILL.md。 | P0 | 需求描述 |
| FEAT-FE-04 | 用户范围 | ALL/SELECTED 变更与指定用户维护。 | P0 | 需求描述 |

### 2.3 范围与边界

| 类别 | 内容 |
|---|---|
| In Scope | Skill 列表、导入、详情4 Tabs、版本详情、变更范围、添加/移除指定用户。 |
| Out of Scope | 不改领域语义；组件不裸用 axios/fetch；不增加交互稿未确认的重型能力 |
| 技术债 | 无 |

### 2.4 验收条件

| 场景ID | 功能ID | 测试层级 | 关键真实边界 | 操作步骤 | 预期 UI 结果 |
|---|---|---|---|---|---|
| S-FE-01 | FEAT-FE-01 | E2E | Browser Upload→NFS/DB→UI | 导入合法 Skill | 列表出现新 Skill，默认 SELECTED，当前版本正确 |
| S-FE-02 | FEAT-FE-03 | E2E | Browser→artifact API | 详情导入新版本 | 版本记录新增，Header 当前版本更新 |

异常：

| 场景ID | 功能ID | 测试层级 | 关键真实边界 | 触发条件 | UI 表现 |
|---|---|---|---|---|---|
| E-FE-01 | FEAT-FE-01 | E2E | Validation API→Modal | 上传非法 ZIP | Modal 保留并显示本地化校验错误 |
| E-FE-02 | FEAT-FE-04 | integration | Grant API→UI | 添加指定用户失败 | 保持当前 Tab，Toast 提示 |

## 3. 前端技术设计

### 3.1 技术选型

- React 18.3.x + TypeScript + Vite；
- `@douyinfe/semi-ui` 2.84.x；
- react-router-dom 6.x；
- axios 统一 ApiClient；
- react-i18next，locale 仅 `zh-CN/en-US`；
- 不新增 Redux/Zustand；页面状态由 route/page local state + module hooks 管理；
- 列表统一 `Table` + controlled pagination；详情统一 `SideSheet` + `Tabs`；
- 新增/编辑统一 `Modal` + `Form`；危险操作统一 `Popconfirm`；
- loading/empty/error 使用 `Spin/Skeleton`、`Empty`、公共 ErrorState；
- 所有文案 `t(key)`；ApiClient 自动发送 `X-Locale`。

### 3.2 页面与路由结构

| 页面 | 路由 | 布局 | 说明 |
|---|---|---|---|
| Skill 管理 | `/skills` | ConsoleShell | 列表 + Detail SideSheet；版本详情使用次级 SideSheet/Modal |

### 3.3 组件设计

```text\n<Page>\n├─ <ModuleToolbar/>\n├─ <RemoteTable/>\n└─ <DetailSideSheet/>\n```

| 组件ID | 组件名 | 类型 | 复用来源/去向 | 职责 |
|---|---|---|---|---|
| CMP-01 | `SkillPage` | 容器 | 模块内 | 搜索/范围筛选/分页/详情 |
| CMP-02 | `SkillImportModal` | 容器 | 模块内 | Semi Upload + Form + 校验结果 |
| CMP-03 | `SkillDetailTabs` | 展示 | 模块内 | 基本信息/版本/Agent/用户 |
| CMP-04 | `SelectedUserTable` | 展示 | 可与 MCP 共享模式 | 指定用户添加/移除 |

**必须复用公共组件**：`ConsoleShell / ModuleToolbar / RemoteTable / EntityLink / DetailSideSheet / DetailTabs / FormModal / StatusTag / DateTimeText / ConfirmAction / EmptyState / ErrorState / PaginationFooter / LocaleSwitch`。

#### 3.3.1 每个按钮/操作的设计

| 位置 | 按钮/链接 | Semi 组件 | 层级 | 行为 | Service/API | 二次确认 |
|---|---|---|---|---|---|---|
| 列表左上 | 导入 Skill | `Button` | primary | 打开 ZIP 导入 Modal | `POST /api/v1/skills/import` | 否 |
| 列表右上 | 用户范围筛选 | `Select` | secondary | ALL/SELECTED 筛选 | `GET /api/v1/skills` | 否 |
| 列表操作列 | 复制标识 | `Button` | secondary | 复制 Skill key | `-` | 否 |
| 详情 Header | 变更用户范围 | `Button` | secondary | 打开范围 Modal | `PUT /api/v1/skills/{id}/user-scope` | 否 |
| 详情 Header | 导入新版本 | `Button` | primary | 上传新 Artifact | `POST /api/v1/skills/{id}/artifacts` | 否 |
| 版本记录 | 版本号链接 | `Typography.Text link` | secondary | 打开版本详情 | `GET /api/v1/skills/{id}/artifacts/{artifact_id}` | 否 |
| 指定用户 Tab | 添加指定用户 | `Button` | primary | 打开用户选择器 | `POST /api/v1/skills/{id}/users/{user_id}` | 否 |
| 指定用户行 | 移除 | `Popconfirm + Button` | secondary | 删除 SkillUserGrant | `DELETE /api/v1/skills/{id}/users/{user_id}` | 是 |

统一规则：主创建/保存使用 `Button theme="solid" type="primary"`；危险操作 `Popconfirm`；详情全局操作与关闭 X 同一 Header 行靠右；Tab 内关系操作完成即生效，不需要“保存整个对象”。

### 3.4 组件接口契约

```ts
export interface DetailSideSheetProps {
  visible: boolean;
  title: React.ReactNode;
  subtitle?: React.ReactNode;
  actions?: React.ReactNode;
  activeTab?: string;
  onCancel(): void;
}
```

展示组件 props-in/events-out；API、路由、提交状态由 Page/Hook 管理。

### 3.5 状态与数据流

```text
User Action
 -> Page/Hook
 -> modules/skill-management/services/*.ts
 -> shared apiClient(X-Locale/X-Request-Id)
 -> Backend Envelope
 -> Hook State
 -> Semi Components
```

| Service 方法 | 对应后端接口 | 调用方 |
|---|---|---|
| `listSkills(params)` | `GET /api/v1/skills` | useSkillList |
| `importSkill(file,input)` | `POST /api/v1/skills/import` | SkillImportModal |
| `importArtifact(id,file)` | `POST /api/v1/skills/{id}/artifacts` | SkillImportModal |
| `setUserScope(id,scope)` | `PUT /api/v1/skills/{id}/user-scope` | UserScopeModal |

### 3.6 UI 状态

| 视图 | loading | empty | error | success |
|---|---|---|---|---|
| 列表 | Table loading/Skeleton | Empty + 创建/清筛选 | ErrorState + 重试 | Table + 右下 Pagination |
| 详情 | SideSheet Spin | Tab Empty | Banner/ErrorState | Descriptions/List/Table |
| 表单 | 保存按钮 loading | - | Form 字段错误 + Toast | 关闭 Modal + 局部刷新 |
| 关系操作 | 当前按钮 loading | - | Toast，保持当前 Tab | 局部刷新 |

### 3.7 样式方案

- 保留交互稿信息架构和字段顺序，可用 Semi Token 重新美化；
- 列表页不增加重复标题/说明块；
- Toolbar 左主操作、右搜索筛选；
- 主展示字段点击打开详情；操作列只放真实动作；
- 详情双列基础信息，<900px 降单列；
- 时间统一 `YYYY-MM-DD HH:mm:ss`；
- 禁止散落魔法颜色/间距。

### 3.8 可访问性与兼容性

Semi Form required/rules；Modal/SideSheet 焦点管理；图标按钮 aria-label；Chrome/Edge 企业当前版本为主，Safari 做开发兼容验证。

## 4. 风险与依赖

- 前置：01-platform-foundation, 02-user-identity, 04-project-platform；
- 风险：硬编码中文、重复造公共 SideSheet/Toolbar、前端 N+1；
- 应对：i18n key 检查、公共组件依赖、列表 API 聚合字段。

## Spec Compliance Matrix

| Spec/Rule | enforcement | 设计影响 | 设计落点 | 验证场景 | 状态/N/A 理由 |
|---|---|---|---|---|---|
| `mss-platform#RULE-I18N-001` | required | 后端错误和前端页面支持 zh-CN/en-US；新增业务仅增加配置。 | §3.3/§3.5 | S-FE-01 + verifier | applied |
| `mss-platform#RULE-UI-001` | required | Console 使用 React + Semi；左上操作、右上搜索筛选、右下分页；主展示字段打开详情。 | §3.3/§3.5 | S-FE-01 + verifier | applied |
| `mss-platform#RULE-UI-DETAIL-001` | required | 详情 SideSheet 标题/副标题左侧，操作按钮与关闭 X 同行靠右，Tabs 在其下。 | §3.3/§3.5 | S-FE-01 + verifier | applied |
| `mss-platform#RULE-AUTH-001` | required | User→Agent；Agent→Skill/MCP；SELECTED 再叠加资源用户 Grant；不建三元授权。 | §3.3/§3.5 | S-FE-01 + verifier | applied |
| `mss-platform#RULE-SKILL-001` | required | NFS-backed RWX PVC 为 Artifact 权威源；Runtime/Worker emptyDir 本地 cache；禁止直接从 NFS 执行 Python。 | §3.3/§3.5 | S-FE-01 + verifier | applied |
| `mss-platform#RULE-FRONT-001` | required | 前端 API 只经 services/；组件不裸用 axios/fetch；文案只用 i18n key。 | §3.3/§3.5 | S-FE-01 + verifier | applied |
| `mss-platform#RULE-TEST-001` | required | 跨 API/DB/Runtime/Browser 的关键流程必须 E2E，列出不得 mock 的真实边界。 | §3.3/§3.5 | S-FE-01 + verifier | applied |
