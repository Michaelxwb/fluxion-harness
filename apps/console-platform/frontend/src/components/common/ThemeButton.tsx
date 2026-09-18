import { IconMoon, IconSun } from '@douyinfe/semi-icons';
import { Button, Tooltip } from '@douyinfe/semi-ui';
import { useTranslation } from 'react-i18next';

import type { ThemeMode } from '../../theme';

export interface ThemeButtonProps {
  mode: ThemeMode;
  onToggle(): void;
}

export function ThemeButton({ mode, onToggle }: ThemeButtonProps) {
  const { t } = useTranslation();
  return (
    <Tooltip content={t(mode === 'dark' ? 'common.theme.light' : 'common.theme.dark')}>
      <Button
        theme="borderless"
        type="tertiary"
        icon={mode === 'dark' ? <IconSun /> : <IconMoon />}
        aria-label="theme-toggle"
        data-testid="theme-toggle"
        onClick={onToggle}
      />
    </Tooltip>
  );
}
