/**
 * CMP-04：分组面板（前端设计 §3.3/§3.4）。
 *
 * 右侧单分组面板：一次只渲染一个分组（页面纵向深度不随分组数增长）。头部是分组标题 +
 * 生效方式标签 + 资源覆盖提示；生效方式是分组级事实（后端 `_GROUP_APPLIES_TO`），字段级
 * 只在**与分组不同**时才标注（见 CMP-05），不再逐字段重复。纯展示（值/错误/禁用都由 props
 * 传入，变更经 `onFieldChange` 上抛）。字段渲染完全交给 CMP-05。
 */

import { Tag } from '@douyinfe/semi-ui';
import { useTranslation } from 'react-i18next';

import { appliesToLabelKey } from '../appliesTo';
import type { SettingsGroupMeta, SettingsValues } from '../types';
import { SettingsFieldRow } from './SettingsFieldRow';
import { OverrideBadge } from './OverrideBadge';

export interface SettingsGroupPanelProps {
  group: SettingsGroupMeta;
  values: SettingsValues;
  errors: Record<string, string>;
  disabled?: boolean;
  onFieldChange(path: string, value: unknown): void;
}

export function SettingsGroupPanel({
  group,
  values,
  errors,
  disabled,
  onFieldChange
}: SettingsGroupPanelProps) {
  const { t } = useTranslation();
  const labelKey = appliesToLabelKey(group.appliesTo);
  const appliesLabel = labelKey ? t(labelKey) : group.appliesTo;
  // API 的覆盖计数按分组下发（stamp 在每个字段上），取组内最大值即为分组事实。
  const overrideCount = group.fields.reduce(
    (max, field) => Math.max(max, field.overriddenByResources),
    0
  );

  return (
    <div className="settings-group" data-testid={`settings-group-${group.key}`}>
      <div className="settings-panel-header">
        <span className="settings-panel-title">{t(group.labelKey)}</span>
        <Tag size="small" type="ghost">
          {appliesLabel}
        </Tag>
        <OverrideBadge count={overrideCount} />
      </div>
      <div className="settings-group-body">
        {group.fields.map((field) => (
          <SettingsFieldRow
            key={field.path}
            meta={field}
            groupAppliesTo={group.appliesTo}
            value={values[field.path]}
            error={errors[field.path]}
            disabled={disabled}
            onChange={(value) => onFieldChange(field.path, value)}
          />
        ))}
      </div>
    </div>
  );
}
