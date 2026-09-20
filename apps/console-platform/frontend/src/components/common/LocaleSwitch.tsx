import { IconGlobe } from '@douyinfe/semi-icons';
import { Button, Tooltip } from '@douyinfe/semi-ui';
import { useTranslation } from 'react-i18next';

import { changeLocale, currentLocale, type SupportedLocale } from '../../i18n';

export function LocaleSwitch() {
  const { t } = useTranslation();
  const locale = currentLocale();
  const next: SupportedLocale = locale === 'zh-CN' ? 'en-US' : 'zh-CN';
  const nextLabel = t(next === 'en-US' ? 'common.language.en' : 'common.language.zh');
  return (
    <Tooltip content={nextLabel}>
      <Button
        theme="borderless"
        type="tertiary"
        icon={<IconGlobe />}
        data-testid="locale-switch"
        aria-label={nextLabel}
        onClick={() => changeLocale(next)}
      />
    </Tooltip>
  );
}
