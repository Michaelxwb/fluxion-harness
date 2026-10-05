/**
 * CMP-03：生效语义提示（前端设计 §3.3/§3.4）。
 *
 * 保存语义对管理员有用但撑不起一条横幅：渲染为工具栏下方的一行浅色说明，不再用 Banner。
 */

import { IconInfoCircle } from '@douyinfe/semi-icons';
import { useTranslation } from 'react-i18next';

export function SettingsNotice() {
  const { t } = useTranslation();
  return (
    <div className="settings-notice" data-testid="settings-notice">
      <IconInfoCircle size="small" className="settings-notice-icon" />
      <span>{t('settings.notice')}</span>
    </div>
  );
}
