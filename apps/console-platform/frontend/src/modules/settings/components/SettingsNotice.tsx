/**
 * CMP-03：生效语义提示条（前端设计 §3.3/§3.4）。静态展示。
 */

import { Banner } from '@douyinfe/semi-ui';
import { useTranslation } from 'react-i18next';

export function SettingsNotice() {
  const { t } = useTranslation();
  return (
    <Banner
      type="info"
      closeIcon={null}
      className="settings-notice"
      data-testid="settings-notice"
      description={t('settings.notice')}
    />
  );
}
