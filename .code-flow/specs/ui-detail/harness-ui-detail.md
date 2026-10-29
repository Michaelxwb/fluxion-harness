---
id: harness-ui-detail
description: 详情 SideSheet 与表格
stages:
- design
- plan
- code
- review
enforcement: required
verifiers:
- rule: RULE-ui-detail-001
  type: command
  config:
    argv:
    - bash
    - -lc
    - uv run pytest -q tests/frontend/test_detail_sidesheet_contract.py tests/frontend/test_form_layout_contract.py && npm --prefix
      apps/console-platform/frontend run typecheck
    cwd: .
    timeout: 600
---

# harness-ui-detail

## Rules

- [RULE-ui-detail-001] 详情使用 SideSheet：标题与副标题居左，关闭 X 同行靠右；列表对象级操作集中到主列表最后一列「操作」，详情不重复渲染，Tabs 位于其下；关系操作保存后立即影响后续新 Run/Task。

## Conventions

所有详情（含历史版本、工具等嵌套详情）通过公共 `DetailSideSheet` 的 `.detail-title` 统一标题样式：桌面 18px、窄屏 16px、700 字重、24px 行高和主文本色；副标题使用 12px、400 字重、18px 行高及次级文本色。长标题允许换行，关闭按钮和标题附属图标保持可见。

✅：`e2e/tests/detail-layout.spec.ts` 验证各模块在明暗主题与窄屏下的标题计算样式、嵌套历史版本标题及带范围图标的长标题换行。

❌：各详情自行设置标题字重，或用扩大字号替代加粗，造成详情间层级不一致。

对象动作（编辑、删除、测试、范围调整、版本导入、Task 取消、Schedule 暂停/恢复）放主列表；标签简化为「编辑」「删除」「测试」「范围」「导入」「刷新」「取消」等。关联业务操作仍在详情 Tab（绑定/解绑、授权/撤销、凭据管理），共享凭据仍是平台凭据 Tab 中的单例卡片。Run 无独立列表，保留详情 Header「刷新」，刷新 Run 和已加载的操作记录。

Agent 基本信息中的审计预览称为「最近活动」，列名为时间/操作用户/操作类型/操作对象/结果；按 Agent 资源 ID 读取最近六条审计记录。动作与对象使用中英文词条（如 CREATE → 创建、AGENT_ACCESS_GRANT → 用户授权、AGENT_SKILL_BINDING → Skill 绑定），结果复用审计结果词条。已知枚举显示友好名称，原始码保留在单元格 title；值域开放，未登记枚举原样显示，不显示空白或伪造含义。

Run「事件轮廓」使用 Semi Timeline：概览显示当前展示的事件数、模型/工具调用开始次数及首尾事件覆盖时长；截断时统计仍只覆盖已展示部分。事件按运行/消息/模型/工具/上下文/产物分类，通过图标和文本区分，失败/等待/完成使用状态色。展示序号从 01 开始，原始会话 seq 保留在 title；完整时间复用 `DateTimeText`，相对时间取 Run 开始时间（缺失或无效则取首事件），间隔取相邻展示事件的时间差。无效或负时差不伪造耗时；轮廓保持结构字段；不配对缺乏关联标识的调用。

✅：`e2e/tests/run-timeline-layout.spec.ts` 验证明暗主题、中英文、窄屏、跳号、产物、异常事件、时间缺失、截断统计与刷新更新。

❌：把会话 seq 当作 Run 内序号；将相邻事件间隔标成调用耗时，或用前 200 条事件的统计声称完整 Run 统计。

Run 时间轴节点支持点击及 Enter/Space 展开，带 aria-expanded / aria-controls。节点首次展开调用 `GET /api/v1/runs/{run_id}/events/{seq}`；折叠再展开复用本次 Run 缓存，刷新保留展开状态并重新加载已展开节点，未展开节点不请求。节点独立显示加载、失败重试、不可用及缺失明细；关闭/切换 Run/刷新使旧请求失效。消息摘要、参数、附件、真实调用耗时、模型 Token、操作/任务当前状态与压缩统计通过白名单 DTO 展示并本地化；已知状态与压缩层不得直接展示枚举码，未知值保留原样。文本 13px、保留换行、最长 12,000 字符并明确截断，复制按钮复制当前展示的脱敏内容；不执行 HTML，不下载产物正文。

读取时关联现有事件与审计，所有关联均限定租户、Run/Task 归属与软删除条件。模型开始/结束事件只写 `model_audit_id`，与对应模型审计主键相同；每次重试使用独立 ID，摘要模型独立审计，不冗余模型事实或猜测历史配对。后台任务链接复用已有 Run→Task 详情切换。

模型节点同时读取 Run 的冻结快照，仅投影 model_id/protocol/model_revision，不用当前模型配置替换历史信息，不回传 params/base_url 或凭据。精确关联成功时显示该次审计的模型、状态、尝试、耗时和 Token；缺少可用关联时明确提示，并独立展示本次 Run 已有全部模型审计的汇总（含重试与摘要调用）。汇总限定同租户、同 Run、未软删；Token 与耗时只加总已记录值，全为缺失时显示「未记录」，真实零值保留为零。非终态 Run 的模型开始节点带有效关联 ID、尚无审计时提示未就绪或暂不可用，可稍后刷新重试；完成节点、无效引用与终态缺失按不可关联处理。不把运行汇总伪装成节点结果。

✅：`tests/console_platform/test_model_event_context.py` 验证冻结信息、历史汇总、关联未就绪、缺失/零值与隔离；浏览器 S-25 验证精确节点无运行汇总，S-26 验证历史节点显示明确范围的汇总。

✅：`tests/console_platform/test_run_event_details.py` 验证真实 DB 关联、租户/软删/非法引用与脱敏；`tests/agent_runtime/test_execution_audit.py` 验证逐次尝试 ID；`e2e/tests/run-observability.spec.ts` S-25/S-26/E-23/E-24 验证真实浏览器按需加载、缓存、刷新、重试、迟到响应与 Task 切换。展开布局在 `run-timeline-layout.spec.ts` 中覆盖中英文、明暗主题与窄屏。

❌：加载轮廓时为全部节点发明细请求；把历史节点发生状态与当前操作/任务状态混为一谈；API 回原始事件/推理/凭据/存储路径，或缺少模型关联时按最近时间拼接。

✅：创建 / 用户授权 / 成功；空态「暂无活动记录」。`e2e/tests/detail-layout.spec.ts` 验证中英文、主题、未知枚举回退与只读；Agent S-09 验证真实创建审计。

❌：把配置与授权审计称为「最近运行」，或直接向用户展示已知 CREATE / AGENT_ACCESS_GRANT 编码。

Skill 基本信息将描述移出字段栅格，与 SKILL.md 放入同一个「技能内容」区；正文默认 Markdown 预览，可切换原文，两个模式都通过公共 `CopyButton` 复制完整原文。当前版本与历史版本共用 `SkillMarkdownViewer`，无正文时显示空态且不提供复制。Markdown 仅以 `format="md"` 渲染，不执行 MDX/HTML，链接仅允许 HTTP(S) 与 mailto，表格完整显示且局部横向滚动。

Markdown 预览字号与详情页协调：正文/列表/表格 14px，代码 13px；H1–H6 分别 20/18/16/15/14/14px，标题使用 600 字重，首个标题不留额外顶部空白。样式仅限定在预览区，并覆盖 Semi Typography 的实际选择器优先级；当前版本与历史版本一致。

✅：`e2e/tests/detail-layout.spec.ts` 在中英文、明暗主题与窄屏检查浏览器计算后的字号，并验证历史版本标题字号。

❌：仅声明低优先级 `h1` 样式，渲染仍采用 Semi 的 32px 标题；只检查 CSS 源码文字而未检查实际计算样式。

✅：标题旁用范围图标（所有用户 / 指定用户），Tooltip 与 aria-label 保留包含 Agent 使用权前提的完整说明；图标可键盘聚焦。ALL 范围的指定用户 Tab 只显示「无需单独指定用户」，不重复访问规则。

❌：描述挤在半宽字段格内、正文固定 240px 高度；预览时复制丢失 Markdown 格式；基本信息与用户 Tab 各放一条相同的范围 Banner。

复制入口统一 `CopyButton` 图标 + Tooltip + aria-label，使用 `CopyableText` 紧邻实际被复制的字段；短 ID 可展示前 8 位，但复制完整值。复制点击阻止事件冒泡，剪贴板失败必须可见（Toast），不得复制占位 `-`。

✅：`{ ...actionColumn(160), title: t('agent.columns.actions'), render: ... }` 作为最后一列；无 actions 的表格不增加固定列。

❌：按翻译后的表头文本猜测操作列；审计事件的 `action` 数据字段不能当成操作按钮列。

详情基本信息统一「分区标题 + `DetailGrid` 双列标签栅格 + 状态 `Tag`」，需要说明时附 `Banner`；统计概览用 `MetricCards`（用户详情：授权/凭据/身份/记忆）。
关系型 Tab 统一「工具行（主操作）→ 分区标题 → 表格 → 底部说明文案」：主操作按钮置顶左对齐（`Button theme="solid" type="primary"`，删除/解绑等操作通过 `ConfirmAction` 确认），分区标题复用 Tab 名，表格列与交互稿一致（授权 Tab 含授权时间；IM Tab 含派生启用状态；记忆 Tab 含来源与更新时间），说明文案用 `.detail-hint`。需要选择对象的主操作（如授权 Agent）以 `FormModal` 承载：弹窗内给出可选对象与操作语义说明，无可选项时展示 `当前所有 Agent 均已授权` 且确认按钮禁用。
表单字段布局以交互稿为准：双列栅格 `.form-grid`（控件 100% 同宽、按稿顺序成对，动态/配置字段配 `.form-section-title`/`.form-section-hint` 分区说明）；详情基本信息用 `DetailGrid` 双列，禁止宽窄混排与裸 schema key。本 spec 是表单布局的唯一 owner（verifier 跑 `tests/frontend/test_form_layout_contract.py`），`harness-ui.md` 只引用不重复。
用户详情四个关系 Tab 复用 `.app-table-fit` 与 `scroll.x: '100%'`：Agent 名称/标识、项目平台名称、IM 外部用户 ID/Bot ID、记忆内容分配剩余空间；状态、时间和操作紧凑定宽。最小表宽依次为 680/832/880/880px，标准 920px 详情面板内均铺满且不横向滚动；窄屏保留表内滚动与右侧固定操作。长文本通过 `TableText` 单行省略并保留完整 title，时间、状态和操作不截断。

✅：`e2e/tests/user-detail-layout.spec.ts` 覆盖四个 Tab 的长短数据、明暗主题、中英文、桌面铺满及窄屏固定操作。

❌：每列均使用默认 176px、操作列使用默认 160px，导致状态列空白过多、IM 时间列无法同时展示。

用户记忆来源按运行时 `author` 枚举本地化：`USER` → 用户已确认（User confirmed）、`ADMIN` → 管理员录入（Admin entry）、`AGENT` → Agent 自动归纳（Agent inferred）。译文在本次渲染内生成，切换语言立即更新；通过 `TableText.title` 保留原始编码。未登记的历史/未知取值原样显示，不推断其来源含义。

✅：`e2e/tests/user-detail-layout.spec.ts` 验证三种来源的中英文文案、原始编码提示、同页切换语言与历史/未知值回退。

❌：直接展示已知 `USER`/`ADMIN`/`AGENT` 编码；把历史取值（如旧的 `USER_EXPLICIT`/`AGENT_INFERRED`）当成当前枚举解释。

主列表与详情表格统一使用 `--semi-color-bg-1` 不透明底色，表头和悬停行在该底色上叠加 `--semi-color-fill-0`，普通列与固定操作列在浅色、深色主题中均保持同色，横向滚动不能透出下层数据。详情单元格不折行（`.semi-sidesheet .semi-table-*` 生效范围仅限详情）、含行操作时最后一列表头为「操作」，通过公共 `actionColumn` + `DataTable`（主列表经 `RemoteTable`）提供列宽、横向滚动和 `fixed: right`；当表格宽度不足以同时容纳操作列与 200px 数据时，操作收进右侧固定 56px「更多」菜单；列名与交互稿一致（Agent 名称/标识、通道、最近活动时间等）。

✅：固定列和普通列共用行底色，悬停整行一起变色；`e2e/support/table-surface.ts` 验证底色、悬停、表头与滚动后的固定位置。

❌：只给操作单元格设置 `--semi-color-bg-2`，导致操作列呈现独立色带；使用透明背景导致滚动数据透出。

✅ 正确：

```tsx
<div className="detail-section-title">{t('user.detail.basicTitle')}</div>
<DetailGrid items={[{ label: t('user.detail.name'), value: user.display_name }]} />
<MetricCards items={[{ label: t('user.metric.agent'), value: user.agent_grant_count, hint: t('user.metric.agentHint') }]} />
```

❌ 错误：

```tsx
<Descriptions data={[{ key: 'name', value: user.display_name }]} />  // 无分区标题、无状态 Tag、无双列栅格
```

详情 `SideSheet` 的公共契约（机器钉死在 `tests/frontend/test_detail_sidesheet_contract.py`）：

- **宽度固定 `width={920}`**，由 `components/common/DetailSideSheet.tsx` 统一给定，各模块不得自设宽度（老模块迁入公共组件时以公共组件为准）。机检：`tests/frontend/test_detail_sidesheet_contract.py`（宽度断言）。

  ❌：模块自己传 `width={720}`/`width={1080}` 覆盖公共组件

- **`actions` 必须条件渲染**：无页内刷新操作时不渲染空操作区（`{props.actions ? props.actions : null}`），避免标题行右侧留一块空白。机检：`tests/frontend/test_detail_sidesheet_contract.py::test_detail_sidesheet_renders_no_empty_action_area`。
- **危险/行内确认操作统一走公共 `ConfirmAction`**（内部 `Popconfirm`；触发按钮固定 `type="primary"`，确认/取消按钮也使用 primary；详情行内默认 `theme="borderless"`，外层按钮需要抢眼时显式传 `theme="light"`）。删除、解绑、撤销、移除与清空操作统一蓝色，通过具体确认文案表达操作含义；组件不提供独立危险色参数。不要在模块里手写 `Popconfirm` + `Button` 组合——确认/取消文案与按钮样式靠它统一（使用点如 `user-identity/UserDetailTabs.tsx`）。
- **模块内「状态 → 颜色」映射按模块收敛到唯一一份 options**，列表列、筛选下拉、结果 Modal、详情共用同一份口径（参考 `model-management/statusOptions.ts`：`ModelDetailSideSheet` 与 `ModelPage` 同 import 同一份；机检 `tests/frontend/test_detail_sidesheet_contract.py` 的 statusOptions 断言）。新模块新增状态枚举时，只允许新增一处映射；不得让同一枚举在多处各写一套。

  ⚠️ 本约束**只要求「按模块收敛」**，不是「全站必须用 `StatusTag`」：`agent-management/AgentDetailSideSheet.tsx`、`skill-management/SkillDetailSideSheet.tsx` 仍有裸用 `<Tag color={...}>` 的存量写法，属待收敛项，不作为违规判据。

## Avoid

- 违反上述任一规则的实现必须修复；与此 Spec 冲突的文档以本 Spec 与 `docs/` V1.4 为准。
