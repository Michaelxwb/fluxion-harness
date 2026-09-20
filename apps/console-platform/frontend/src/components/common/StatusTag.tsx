import { Tag } from '@douyinfe/semi-ui';
import type { ReactNode } from 'react';

export type StatusColor = 'green' | 'grey' | 'red' | 'amber' | 'blue';

export interface StatusTagOption {
  color: StatusColor;
  label: ReactNode;
}

export interface StatusTagProps {
  status: string | number | boolean;
  options: Record<string, StatusTagOption>;
  fallback?: StatusTagOption;
}

export function StatusTag({ status, options, fallback }: StatusTagProps) {
  const key = String(status);
  const option = options[key] ?? fallback ?? { color: 'grey' as const, label: key };
  return <Tag color={option.color}>{option.label}</Tag>;
}
