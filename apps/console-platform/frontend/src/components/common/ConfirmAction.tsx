import { Button, Popconfirm } from '@douyinfe/semi-ui';
import type { CSSProperties, ReactNode } from 'react';
import { useTranslation } from 'react-i18next';

export interface ConfirmActionProps {
  title: ReactNode;
  onConfirm(): void;
  children: ReactNode;
  danger?: boolean;
  testId?: string;
  style?: CSSProperties;
  onOpenChange?(visible: boolean): void;
  /** Button theme; defaults to borderless for in-detail row actions. Object-level header
   *  delete buttons pass `light` so a destructive action keeps its visual weight. */
  theme?: 'borderless' | 'light' | 'outline' | 'solid';
}

export function ConfirmAction({
  title,
  onConfirm,
  children,
  danger = false,
  testId,
  style,
  onOpenChange,
  theme = 'borderless'
}: ConfirmActionProps) {
  const { t } = useTranslation();
  return (
    <Popconfirm
      title={title}
      okText={t('common.confirm')}
      cancelText={t('common.cancel')}
      onConfirm={onConfirm}
      onVisibleChange={(visible: boolean) => onOpenChange?.(visible)}
    >
      <Button theme={theme} type={danger ? 'danger' : 'tertiary'} data-testid={testId} style={style}>
        {children}
      </Button>
    </Popconfirm>
  );
}
