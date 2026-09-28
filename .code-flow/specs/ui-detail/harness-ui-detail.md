---
id: harness-ui-detail
description: Agent Harness 通用平台规则：ui-detail
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

- [RULE-ui-detail-001] 详情使用 SideSheet：标题与副标题居左，对象级操作与关闭 X 同一行靠右，Tabs 位于其下；关系操作保存后立即影响后续新 Run/Task。

## Conventions

详情基本信息统一「分区标题 + `DetailGrid` 双列标签栅格 + 状态 `Tag`」，需要说明时附 `Banner`；统计概览用 `MetricCards`（用户详情：授权/凭据/身份/记忆）。
关系型 Tab 统一「工具行（主操作）→ 分区标题 → 表格 → 底部说明文案」：主操作按钮置顶左对齐（`Button theme="solid"`，危险操作 `type="danger"` + `Popconfirm`），分区标题复用 Tab 名，表格列与交互稿一致（授权 Tab 含授权时间；IM Tab 含派生启用状态；记忆 Tab 含来源与更新时间），说明文案用 `.detail-hint`。需要选择对象的主操作（如授权 Agent）以 `FormModal` 承载：弹窗内给出可选对象与操作语义说明，无可选项时展示 `当前所有 Agent 均已授权` 且确认按钮禁用。
表单字段布局以交互稿为准：双列栅格 `.form-grid`（控件 100% 同宽、按稿顺序成对，动态/配置字段配 `.form-section-title`/`.form-section-hint` 分区说明）；详情基本信息用 `DetailGrid` 双列，禁止宽窄混排与裸 schema key。
详情内表格统一：表头 `--semi-color-fill-0` 浅底、数据行透明、单元格不折行（`.semi-sidesheet .semi-table-*` 生效范围仅限详情）、最后一列表头为「操作」；列名与交互稿一致（Agent 名称/标识、通道、最近活动时间等）。

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

- **宽度固定 `width={920}`**，由 `components/common/DetailSideSheet.tsx` 统一给定，各模块不得自设宽度（老模块迁入公共组件时以公共组件为准）。机检：`tests/frontend/test_detail_sidesheet_contract.py:89-90`。

  ❌：模块自己传 `width={720}`/`width={1080}` 覆盖公共组件

- **`actions` 必须条件渲染**：无对象级操作时不渲染空操作区（`{props.actions ? props.actions : null}`），避免标题行右侧留一块空白。机检：`tests/frontend/test_detail_sidesheet_contract.py:32-34`。
- **危险/行内确认操作统一走公共 `ConfirmAction`**（内部 `Popconfirm`；`danger` 时 `type="danger"`，详情行内默认 `theme="borderless"`，外层按钮需要抢眼时显式传 `theme="light"`）。不要在模块里手写 `Popconfirm` + `Button` 组合——确认/取消文案与危险色都靠它统一（使用点如 `user-identity/UserDetailTabs.tsx`）。
- **模块内「状态 → 颜色」映射按模块收敛到唯一一份 options**，列表列、筛选下拉、结果 Modal、详情共用同一份口径（参考 `model-management/statusOptions.ts`：`ModelDetailSideSheet` 与 `ModelPage` 同 import 同一份；机检 `tests/frontend/test_detail_sidesheet_contract.py:66-69`）。新模块新增状态枚举时，只允许新增一处映射；不得让同一枚举在多处各写一套。

  ⚠️ 本约束**只要求「按模块收敛」**，不是「全站必须用 `StatusTag`」：`agent-management/AgentDetailSideSheet.tsx`、`skill-management/SkillDetailSideSheet.tsx` 仍有裸用 `<Tag color={...}>` 的存量写法，属待收敛项，不作为违规判据。

## Avoid

- 违反上述任一规则的实现必须修复；与此 Spec 冲突的文档以本 Spec 与 `docs/` V1.4 为准。
