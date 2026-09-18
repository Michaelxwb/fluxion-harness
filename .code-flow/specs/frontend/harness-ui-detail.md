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
    - uv run pytest -q tests/frontend/test_detail_sidesheet_contract.py && npm --prefix
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

## Avoid

- 违反上述任一规则的实现必须修复；与此 Spec 冲突的文档以本 Spec 与 `docs/` V1.4 为准。
