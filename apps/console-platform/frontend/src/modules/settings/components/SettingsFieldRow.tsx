/**
 * CMP-05：单个设置项行（前端设计 §3.3/§3.4）。
 *
 * 单行布局：标签与参考信息同行在左（默认值/单位/范围，浅色小字），控件在右。范围（`min`/`max`）
 * 与默认值只来自 `meta`，本组件不写任何默认值或范围字面量（RISK-FE-01，避免前端成为第二套
 * 默认源）。生效方式标签由纯函数 `appliesToLabelKey` 映射，未知取值不兜底（B-06）；标签只在
 * 字段与所在分组的生效方式**不同**时展示——分组级标签由 CMP-04 统一展示，不逐字段重复。
 */

import { Input, InputNumber, Select, Switch, Tag } from '@douyinfe/semi-ui';
import { useTranslation } from 'react-i18next';

import { appliesToLabelKey } from '../appliesTo';
import { controlKind } from '../hooks/usePlatformSettings';
import type { AppliesTo, SettingsFieldMeta } from '../types';
import { OverrideBadge } from './OverrideBadge';

export interface SettingsFieldRowProps {
  meta: SettingsFieldMeta;
  /** 所在分组的生效方式：字段级标签只在偏离分组时展示。 */
  groupAppliesTo: AppliesTo;
  value: unknown;
  error?: string;
  disabled?: boolean;
  onChange(value: unknown): void;
}

type Translate = (key: string, options?: Record<string, unknown>) => string;

function formatValue(value: unknown, t: Translate): string {
  if (value === null || value === undefined) {
    return t('settings.value.none');
  }
  if (typeof value === 'boolean') {
    return value ? t('settings.bool.true') : t('settings.bool.false');
  }
  return String(value);
}

/** 参考信息里的范围片段：双侧区间 / 仅下界 / 仅上界；无范围返回 null。 */
function rangeHint(meta: SettingsFieldMeta, t: Translate): string | null {
  if (meta.min !== undefined && meta.max !== undefined) {
    return t('settings.field.range', { min: meta.min, max: meta.max });
  }
  if (meta.min !== undefined) {
    return t('settings.field.rangeMin', { min: meta.min });
  }
  if (meta.max !== undefined) {
    return t('settings.field.rangeMax', { max: meta.max });
  }
  return null;
}

export function SettingsFieldRow({
  meta,
  groupAppliesTo,
  value,
  error,
  disabled,
  onChange
}: SettingsFieldRowProps) {
  const { t } = useTranslation();
  const kind = controlKind(meta);
  const inputId = `settings-input-${meta.path}`;
  const labelKey = appliesToLabelKey(meta.appliesTo);
  const appliesLabel = labelKey ? t(labelKey) : meta.appliesTo;
  const range = rangeHint(meta, t);
  const hint = [
    `${t('settings.field.default')}: ${formatValue(meta.defaultValue, t)}`,
    meta.unitKey ? `(${t(meta.unitKey)})` : null,
    range
  ]
    .filter((part): part is string => part !== null)
    .join(' · ');

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
        <span className="settings-field-hint">{hint}</span>
      </div>
      <div className="settings-field-control">{control}</div>
      {meta.appliesTo !== groupAppliesTo ? (
        <div className="settings-field-flags">
          <Tag size="small" type="ghost">
            {appliesLabel}
          </Tag>
          <OverrideBadge count={meta.overriddenByResources} />
        </div>
      ) : null}
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
