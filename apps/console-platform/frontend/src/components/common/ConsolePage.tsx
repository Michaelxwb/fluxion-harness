import type { ReactNode } from 'react';

export interface PageHeaderProps {
  title: ReactNode;
  description?: ReactNode;
  extra?: ReactNode;
}

export function PageHeader({ title, description, extra }: PageHeaderProps) {
  return (
    <div className="page-header">
      <div>
        <h1 className="page-header-title">{title}</h1>
        {description ? <p className="page-header-description">{description}</p> : null}
      </div>
      {extra ? <div>{extra}</div> : null}
    </div>
  );
}

export interface PageSectionProps {
  children: ReactNode;
}

export function PageSection({ children }: PageSectionProps) {
  return <section className="page-section">{children}</section>;
}
