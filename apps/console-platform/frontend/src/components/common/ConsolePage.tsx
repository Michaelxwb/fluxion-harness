import { Card, Typography } from '@douyinfe/semi-ui';
import type { ReactNode } from 'react';

const { Title, Text } = Typography;

export interface PageHeaderProps {
  title: ReactNode;
  description?: ReactNode;
  extra?: ReactNode;
}

export function PageHeader({ title, description, extra }: PageHeaderProps) {
  return (
    <header className="page-header">
      <div className="page-header-heading">
        <Title heading={4} className="page-header-title">
          {title}
        </Title>
        {description ? (
          <Text type="tertiary" className="page-header-description">
            {description}
          </Text>
        ) : null}
      </div>
      {extra ? <div className="page-header-extra">{extra}</div> : null}
    </header>
  );
}

export interface PageSectionProps {
  title?: ReactNode;
  extra?: ReactNode;
  children: ReactNode;
}

export function PageSection({ title, extra, children }: PageSectionProps) {
  return (
    <Card className="page-section" bordered shadows="hover">
      {title || extra ? (
        <div className="page-section-header">
          {title ? <span className="page-section-title">{title}</span> : null}
          {extra ? <div>{extra}</div> : null}
        </div>
      ) : null}
      {children}
    </Card>
  );
}
