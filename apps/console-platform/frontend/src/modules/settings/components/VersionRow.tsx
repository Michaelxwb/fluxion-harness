/**
 * CMP-09：单条历史版本行（前端设计 §3.3）。纯展示，回滚经 `onRestore` 上抛。
 */

import { Button, Tag } from '@douyinfe/semi-ui';
import { useTranslation } from 'react-i18next';

import { DateTimeText } from '../../../components/common/DateTimeText';
import type { SettingsRevision } from '../types';

export interface VersionRowProps {
  item: SettingsRevision;
  currentRevision: number;
  resting?: boolean;
  onRestore(revision: number): void;
}

export function VersionRow({ item, currentRevision, resting, onRestore }: VersionRowProps) {
  const { t } = useTranslation();
  const isCurrent = item.revision === currentRevision;
  return (
    <div className="settings-version-row" data-testid={`settings-version-${item.revision}`}>
      <div className="settings-version-main">
        <span className="settings-version-number">
          {t('settings.history.revision')} {item.revision}
          {isCurrent ? (
            <Tag size="small" type="solid" style={{ marginLeft: 8 }}>
              {t('settings.history.current')}
            </Tag>
          ) : null}
        </span>
        <span className="settings-version-time">
          {item.createdAt ? <DateTimeText value={item.createdAt} /> : t('settings.value.none')}
        </span>
        <span className="settings-version-actor">
          {t('settings.history.actor')}: {item.actorDisplay ?? t('settings.value.none')}
        </span>
      </div>
      {item.changedKeys.length > 0 ? (
        <div className="settings-version-keys">
          {t('settings.history.changedKeys')}: {item.changedKeys.join(', ')}
        </div>
      ) : null}
      <div className="settings-version-actions">
        <Button
          theme="borderless"
          type="tertiary"
          size="small"
          disabled={isCurrent || resting}
          data-testid={`settings-restore-${item.revision}`}
          onClick={() => onRestore(item.revision)}
        >
          {t('settings.history.restore')}
        </Button>
      </div>
    </div>
  );
}
