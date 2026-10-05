/**
 * CMP-02：设置页头（前端设计 §3.3/§3.4）。
 *
 * 展示当前版本 / 保存者 / 保存时间与保存、重置、查看历史三个出口；纯展示，事件一律上抛。
 * 无改动（`dirty=false`）或保存中时保存按钮禁用（B-05）。
 */

import { Button } from '@douyinfe/semi-ui';
import { useTranslation } from 'react-i18next';

import { DateTimeText } from '../../../components/common/DateTimeText';

export interface SettingsHeaderProps {
  revision: number;
  updatedBy: string | null;
  updatedAt: string | null;
  dirty: boolean;
  saving: boolean;
  onSave(): void;
  onReset(): void;
  onOpenHistory(): void;
}

export function SettingsHeader(props: SettingsHeaderProps) {
  const { t } = useTranslation();
  const revisionText =
    props.revision > 0
      ? t('settings.header.revision', { revision: props.revision })
      : t('settings.header.neverSaved');
  return (
    <div className="settings-header" data-testid="settings-header">
      <div className="settings-header-meta">
        <span className="settings-header-revision" data-testid="settings-revision">
          {revisionText}
        </span>
        {props.updatedBy ? (
          <span className="settings-header-actor">
            {t('settings.header.updatedBy')}: {props.updatedBy}
          </span>
        ) : null}
        {props.updatedAt ? (
          <span className="settings-header-time">
            {t('settings.header.updatedAt')}: <DateTimeText value={props.updatedAt} />
          </span>
        ) : null}
      </div>
      <div className="settings-header-actions">
        <Button
          theme="borderless"
          type="tertiary"
          data-testid="settings-history-open"
          onClick={props.onOpenHistory}
        >
          {t('settings.header.viewHistory')}
        </Button>
        <Button
          theme="borderless"
          type="tertiary"
          disabled={!props.dirty || props.saving}
          data-testid="settings-reset"
          onClick={props.onReset}
        >
          {t('settings.header.reset')}
        </Button>
        <Button
          theme="solid"
          loading={props.saving}
          disabled={!props.dirty}
          data-testid="settings-save"
          onClick={props.onSave}
        >
          {t('settings.header.save')}
        </Button>
      </div>
    </div>
  );
}
