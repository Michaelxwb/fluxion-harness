import { Button } from '@douyinfe/semi-ui';
import type { ReactNode } from 'react';

export interface EntityLinkProps {
  onClick(): void;
  children: ReactNode;
  testId?: string;
}

export function EntityLink({ onClick, children, testId }: EntityLinkProps) {
  return (
    <Button theme="borderless" data-testid={testId} onClick={onClick}>
      {children}
    </Button>
  );
}
