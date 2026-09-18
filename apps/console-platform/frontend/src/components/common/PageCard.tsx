import { Card } from '@douyinfe/semi-ui';
import type { ReactNode } from 'react';

export interface PageCardProps {
  children: ReactNode;
}

export function PageCard({ children }: PageCardProps) {
  return (
    <div className="page-shell">
      <Card className="page-card" bordered={false}>
        {children}
      </Card>
    </div>
  );
}
