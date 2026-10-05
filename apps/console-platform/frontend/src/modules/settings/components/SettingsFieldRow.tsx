/**
 * CMP-05：单个设置项行（前端设计 §3.3/§3.4）。
 *
 * 按 API 返回的 `type`/`enum` 渲染控件；**范围（`min`/`max`）与默认值只来自 `meta`**，本组件不写
 * 任何默认值或范围字面量（RISK-FE-01，避免前端成为第二套默认源）。生效方式标签由纯函数
 * `appliesToLabelKey` 映射，未知取值不兜底（B-06）。
 */

import { Input, InputNumber, Select, Switch } from '@douyinfe/semi-ui';
import { useTranslation } from 'react-i18next';

import { appliesToLabelKey } from '../appliesTo';
import { controlKind } from '../hooks/usePlatformSettings';
import type { SettingsFieldMeta } from '../types';
import { OverrideBadge } from './OverrideBadge';

export interface SettingsFieldRowProps {
  meta: SettingsFieldMeta;
  value: unknown;
  error?: string;
  disabled?: boolean;
  onChange(value: unknown): void;
}

function formatValue(
  value: unknown,
  t: (key: string) => string
): string {
  if (value === null || value === undefined) {
    return t('settings.value.none');
  }
  if (typeof value === 'boolean') {
    return value ? t('settings.bool.true') : t('settings.bool.false');
  }
  return String(value);
}

export function SettingsFieldRow({ meta, value, error, disabled, onChange }: SettingsFieldRowProps) {
  const { t } = useTranslation();
  const kind = controlKind(meta);
  const inputId = `settings-input-${meta.path}`;
  const labelKey = appliesToLabelKey(meta.appliesTo);
  const appliesLabel = labelKey ? t(labelKey) : meta.appliesTo;

  let control: JSX.Element;
  if (kind === 'switch') {
    control = (
      <Switch
        id={inputId}
        checked={Boolean(value)}
        disabled={disabled}
        onChange={(checked) => onChange(checked)}
      />
    );
  } else if (kind === 'select') {
    control = (
      <Select
        id={inputId}
        value={value === null || value === undefined ? undefined : String(value)}
        disabled={disabled}
        style={{ minWidth: 200 }}
        optionList={(meta.enum ?? []).map((option) => ({ value: option, label: option }))}
        onChange={(option) => onChange(option)}
      />
    );
  } else if (kind === 'number') {
    control = (
      <InputNumber
        id={inputId}
        value={typeof value === 'number' ? value : undefined}
        min={meta.min}
        max={meta.max}
        disabled={disabled}
        style={{ width: 200 }}
        onChange={(next) => onChange(next)}
      />
    );
  } else {
    control = (
      <Input
        id={inputId}
        value={value === null || value === undefined ? '' : String(value)}
        disabled={disabled}
        style={{ width: 260 }}
        onChange={(text) => onChange(text)}
      />
    );
  }

  return (
    <div className="settings-field" data-testid={`settings-field-${meta.path}`}>
      <div className="settings-field-main">
        <label className="settings-field-label" htmlFor={inputId}>
          {t(meta.labelKey)}
        </label>
        {control}
      </div>
      <div className="settings-field-meta">
        <span className="settings-field-default">
          {t('settings.field.default')}: {formatValue(meta.defaultValue, t)}
          {meta.unitKey ? ` (${t(meta.unitKey)})` : ''}
        </span>
        <span className="settings-field-applies">{appliesLabel}</span>
        <OverrideBadge count={meta.overriddenByResources} />
        {meta.min !== undefined || meta.max !== undefined ? (
          <span className="settings-field-range">
            {t('settings.field.range')}: {meta.min ?? '-'} ~ {meta.max ?? '-'}
          </span>
        ) : null}
      </div>
      {error ? (
        <div
          className="settings-field-error"
          role="alert"
          aria-describedby={inputId}
          data-testid={`settings-error-${meta.path}`}
        >
          {error}
        </div>
      ) : null}
    </div>
  );
}
