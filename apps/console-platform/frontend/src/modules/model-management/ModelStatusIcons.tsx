import {
  IconAlertCircle, IconGlobe, IconGlobeStroked, IconHelpCircle,
  IconMinusCircle, IconPlayCircle, IconTickCircle
} from '@douyinfe/semi-icons';
import { Button, Tooltip } from '@douyinfe/semi-ui';
import type { ReactNode } from 'react';
import { useTranslation } from 'react-i18next';

import type { ModelItem } from './services/models';
import { enabledOptions, testStatusOptions } from './statusOptions';

interface ModelStatusIconsProps {
  model: ModelItem;
  saving: boolean;
  onToggle(): void;
}

function StatusIcon(props: { label: string; tone: 'success' | 'muted' | 'error'; children: ReactNode }) {
  return (
    <Tooltip content={props.label}>
      <span role="img" aria-label={props.label} tabIndex={0} className={`model-status-icon model-status-${props.tone}`}>
        {props.children}
      </span>
    </Tooltip>
  );
}

export function ModelStatusIcons({ model, saving, onToggle }: ModelStatusIconsProps) {
  const { t } = useTranslation();
  // 逻辑模型不再持有密钥（执行入口在成员凭据）：这里只表达供方是否已声明，不猜成员/Key 状态
  const declared = model.provider_key !== '';
  const providerLabel = `${t('model.columns.provider')}: ${t(declared ? 'model.provider.declared' : 'model.provider.missing')}`;
  const enabledLabel = `${t('model.columns.enabled')}: ${enabledOptions(t)[String(model.enabled)].label}`;
  const testLabel = `${t('model.columns.lastTestStatus')}: ${testStatusOptions(t)[model.last_test_status].label}`;
  const testTone = model.last_test_status === 'AVAILABLE' ? 'success' : model.last_test_status === 'FAILED' ? 'error' : 'muted';
  const testIcon = model.last_test_status === 'AVAILABLE' ? <IconTickCircle /> : model.last_test_status === 'FAILED' ? <IconAlertCircle /> : <IconHelpCircle />;
  return (
    <div className="model-status-icons">
      <StatusIcon label={providerLabel} tone={declared ? 'success' : 'muted'}>
        {declared ? <IconGlobe /> : <IconGlobeStroked />}
      </StatusIcon>
      <Tooltip content={`${enabledLabel} · ${t(model.enabled ? 'model.status.disable' : 'model.status.enable')}`}>
        <Button
          role="switch"
          aria-checked={model.enabled}
          aria-label={enabledLabel}
          theme="borderless"
          className={`model-status-icon model-status-${model.enabled ? 'success' : 'muted'}`}
          icon={model.enabled ? <IconPlayCircle /> : <IconMinusCircle />}
          loading={saving}
          disabled={saving}
          onClick={onToggle}
        />
      </Tooltip>
      <StatusIcon label={testLabel} tone={testTone}>{testIcon}</StatusIcon>
    </div>
  );
}
