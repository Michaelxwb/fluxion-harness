import type { ReactNode } from 'react';
import { Typography } from '@douyinfe/semi-ui';

const { Title, Text } = Typography;

interface PageContainerProps {
  title: string;
  description?: string;
  children: ReactNode;
}

export function PageContainer({ title, description, children }: PageContainerProps) {
  return (
    <section>
      <div className="page-title-row">
        <div>
          <Title heading={3} style={{ margin: 0 }}>{title}</Title>
          {description ? <Text type="tertiary">{description}</Text> : null}
        </div>
      </div>
      {children}
    </section>
  );
}
