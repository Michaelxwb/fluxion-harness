/**
 * CMP-04：分组面板（前端设计 §3.3/§3.4）。
 *
 * 展示一个分组的标题、生效方式与可折叠的字段列表；纯展示（值/错误/禁用都由 props 传入，
 * 变更经 `onFieldChange` 上抛）。字段渲染完全交给 CMP-05。
 */

import { Collapse, Tag } from '@douyinfe/semi-ui';
import { useTranslation } from 'react-i18next';

import { appliesToLabelKey } from '../appliesTo';
import type { SettingsGroupMeta, SettingsValues } from '../types';
import { SettingsFieldRow } from './SettingsFieldRow';

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

  return (
    <div className="settings-group" data-testid={`settings-group-${group.key}`}>
      <Collapse defaultActiveKey={[]}>
        <Collapse.Panel
          itemKey={group.key}
          header={
            <span className="settings-group-header">
              <span className="settings-group-title">{t(group.labelKey)}</span>
              <Tag size="small" type="ghost">
                {appliesLabel}
              </Tag>
            </span>
          }
        >
          <div className="settings-group-body">
            {group.fields.map((field) => (
              <SettingsFieldRow
                key={field.path}
                meta={field}
                value={values[field.path]}
                error={errors[field.path]}
                disabled={disabled}
                onChange={(value) => onFieldChange(field.path, value)}
              />
            ))}
          </div>
        </Collapse.Panel>
      </Collapse>
    </div>
  );
}
