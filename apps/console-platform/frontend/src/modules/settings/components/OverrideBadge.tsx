/**
 * CMP-06：资源覆盖提示（前端设计 §3.3/§3.4）。
 *
 * `count>0` → 「已被 N 个资源覆盖」；`count=0` → 「无资源覆盖，全部使用平台默认」（§3.6 的 empty 态）。
 */

import { useTranslation } from 'react-i18next';

export interface OverrideBadgeProps {
  count: number;
}

export function OverrideBadge({ count }: OverrideBadgeProps) {
  const { t } = useTranslation();
  if (count > 0) {
    return (
      <span className="settings-override" data-testid="settings-override-count">
        {t('settings.override.count', { count })}
      </span>
    );
  }
  return (
    <span className="settings-override" data-testid="settings-override-none">
      {t('settings.override.none')}
    </span>
  );
}
