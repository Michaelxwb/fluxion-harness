/**
 * CMP-06：资源覆盖提示（前端设计 §3.3/§3.4）。
 *
 * 只在有实际覆盖时提示「已被 N 个资源覆盖」；`count=0` 渲染 null——"没有覆盖"是常态而非
 * 需要通告的状态，逐组重复只会制造噪音。
 */

import { useTranslation } from 'react-i18next';

export interface OverrideBadgeProps {
  count: number;
}

export function OverrideBadge({ count }: OverrideBadgeProps) {
  const { t } = useTranslation();
  if (count <= 0) {
    return null;
  }
  return (
    <span className="settings-override" data-testid="settings-override-count">
      {t('settings.override.count', { count })}
    </span>
  );
}
