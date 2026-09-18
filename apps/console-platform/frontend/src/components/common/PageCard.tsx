import { Card } from '@douyinfe/semi-ui';
import type { ReactNode } from 'react';

export interface PageCardProps {
  title?: ReactNode;
  subtitle?: ReactNode;
  extra?: ReactNode;
  children: ReactNode;
}

export function PageCard({ title, subtitle, extra, children }: PageCardProps) {
  const hasHeader = Boolean(title || subtitle || extra);
  return (
    <div className="page-shell">
      <Card className="page-card" bordered={false}>
        {hasHeader ? (
          <div className="page-header">
            <div>
              {title ? <h1 className="page-header-title">{title}</h1> : null}
              {subtitle ? <p className="page-header-subtitle">{subtitle}</p> : null}
            </div>
            {extra ? <div>{extra}</div> : null}
          </div>
        ) : null}
        {children}
      </Card>
    </div>
  );
}
