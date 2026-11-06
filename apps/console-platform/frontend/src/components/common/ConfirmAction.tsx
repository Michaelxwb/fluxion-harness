import { Button, Popconfirm } from '@douyinfe/semi-ui';
import type { CSSProperties, ReactNode } from 'react';
import { useTranslation } from 'react-i18next';

export interface ConfirmActionProps {
  title: ReactNode;
  onConfirm(): void;
  children: ReactNode;
  testId?: string;
  style?: CSSProperties;
  onOpenChange?(visible: boolean): void;
  /** Button theme; defaults to borderless for list and associated row actions. */
  type?: 'primary' | 'danger';
  theme?: 'borderless' | 'light' | 'outline' | 'solid';
}

export function ConfirmAction({
  title,
  onConfirm,
  children,
  testId,
  style,
  onOpenChange,
  type = 'primary',
  theme = 'borderless'
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
      <Button theme={theme} type={type} data-testid={testId} style={style}>
        {children}
      </Button>
    </Popconfirm>
  );
}
