import { IconRefresh, IconUndo, IconSearch } from '@douyinfe/semi-icons';
import { Button, Tooltip } from '@douyinfe/semi-ui';
import type { ComponentProps } from 'react';
import { useTranslation } from 'react-i18next';

type Action = 'search' | 'reset' | 'refresh';
type Props = Omit<NonNullable<ComponentProps<typeof Button>>, 'children' | 'icon'> & { action: Action };
const ICONS = { search: <IconSearch />, reset: <IconUndo />, refresh: <IconRefresh /> };

export function ListActionButton({ action, ...props }: Props) {
  const { t } = useTranslation();
  const label = t(`common.${action}`);
  return (
    <Tooltip content={label}>
      <Button {...props} aria-label={label} icon={ICONS[action]} />
    </Tooltip>
  );
}
