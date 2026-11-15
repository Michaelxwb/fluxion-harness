import { Button, Popconfirm } from '@douyinfe/semi-ui';
import type { CSSProperties, ReactNode } from 'react';
import { useTranslation } from 'react-i18next';

export interface ConfirmActionProps {
  title: ReactNode;
  onConfirm(): void;
  children?: ReactNode;
  /** 图标按钮：只给图标时用 `ariaLabel` 提供可访问名。 */
  icon?: ReactNode;
  ariaLabel?: string;
  testId?: string;
  style?: CSSProperties;
  onOpenChange?(visible: boolean): void;
  /** Button theme; defaults to borderless for list and associated row actions. */
  type?: 'primary' | 'danger';
  theme?: 'borderless' | 'light' | 'outline' | 'solid';
  size?: 'small' | 'default';
}

export function ConfirmAction({
  title,
  onConfirm,
  children,
  icon,
  ariaLabel,
  testId,
  style,
  onOpenChange,
  type = 'primary',
  theme = 'borderless',
  size = 'default'
}: ConfirmActionProps) {
  const { t } = useTranslation();
  return (
    <Popconfirm
      title={title}
      okText={t('common.confirm')}
      cancelText={t('common.cancel')}
      cancelType="primary"
      okType={type}
      onConfirm={onConfirm}
      onVisibleChange={(visible: boolean) => onOpenChange?.(visible)}
    >
      <Button
        theme={theme}
        type={type}
        size={size}
        icon={icon}
        aria-label={ariaLabel}
        data-testid={testId}
        style={style}
      >
        {children}
      </Button>
    </Popconfirm>
  );
}
