import { IconGlobe } from '@douyinfe/semi-icons';
import { Button, Tooltip } from '@douyinfe/semi-ui';

import { changeLocale, currentLocale, type SupportedLocale } from '../../i18n';

export function LanguageSwitcher() {
  const locale = currentLocale();
  const next: SupportedLocale = locale === 'zh-CN' ? 'en-US' : 'zh-CN';
  return (
    <Tooltip content={next === 'en-US' ? 'English' : '中文'}>
      <Button
        theme="borderless"
        type="tertiary"
        icon={<IconGlobe />}
        data-testid="locale-switch"
        aria-label={next === 'en-US' ? 'English' : '中文'}
        onClick={() => changeLocale(next)}
      />
    </Tooltip>
  );
}
